from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from string import Template
import subprocess
import time
from typing import Any, Callable

import httpx

from .config import AppSettings
from .db import fetch_active_paper_trading_run, fetch_paper_positions
from .services.market_activity import MarketActivityScanner
from .services.paper_trading import PaperTradingCoordinator, PaperTradingEngine
from .services.report_builder import ReportBuilder
from .snapshot_dashboard import build_snapshot_dashboard


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_path(project_root: Path, value: Path) -> Path:
    if value.is_absolute():
        return value
    return project_root / value


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class AISupervisorResult:
    ok: bool
    actions: list[str]
    scan_id: str | None
    scan_age_minutes: float | None
    review_written: bool
    snapshot_written: bool
    message: str


class GeminiReviewer:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=45.0)

    def generate_markdown(self, prompt: str) -> str:
        generation_config: dict[str, object] = {
            "maxOutputTokens": 700,
        }
        if not self.model.startswith("gemini-3"):
            generation_config["temperature"] = 0.2
        response = self._client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000] if exc.response is not None else str(exc)
            raise RuntimeError(f"Gemini request failed: {detail}") from exc

        payload = response.json()
        texts: list[str] = []
        for candidate in payload.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                text = part.get("text")
                if text:
                    texts.append(str(text))
        if not texts:
            raise RuntimeError("Gemini response did not include any text output.")
        return "\n\n".join(texts).strip()


class AISupervisor:
    def __init__(
        self,
        settings: AppSettings,
        check_interval_seconds: int = 3600,
        refresh_if_older_than_minutes: int = 15,
        snapshot_output: Path = Path("sample_outputs/radar_dashboard.html"),
        review_output: Path = Path("automation/inbox/gemini_review.md"),
        prompt_path: Path = Path("automation/prompts/gemini_reviewer.md"),
        log_path: Path = Path("automation/logs/ai_supervisor.log"),
        state_path: Path = Path("automation/state/ai_supervisor_state.json"),
        command_runner: Callable[[list[str], Path], CommandResult] | None = None,
        snapshot_builder: Callable[[Path], Path] | None = None,
        reviewer: GeminiReviewer | None = None,
        paper_trading_engine: PaperTradingCoordinator | PaperTradingEngine | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = _now_utc,
        git_status_provider: Callable[[Path], str] | None = None,
    ) -> None:
        self.settings = settings
        self.check_interval_seconds = max(int(check_interval_seconds), 5)
        self.refresh_if_older_than_minutes = max(int(refresh_if_older_than_minutes), 1)
        self.project_root = _project_root()
        self.snapshot_output = _resolve_path(self.project_root, snapshot_output)
        self.review_output = _resolve_path(self.project_root, review_output)
        self.prompt_path = _resolve_path(self.project_root, prompt_path)
        self.log_path = _resolve_path(self.project_root, log_path)
        self.state_path = _resolve_path(self.project_root, state_path)
        self.command_runner = command_runner or self._run_command
        self.snapshot_builder = snapshot_builder or self._build_snapshot
        self.reviewer = reviewer
        self.paper_trading_engine = paper_trading_engine or (
            PaperTradingCoordinator(settings, now_fn=now_fn) if settings.paper_trading_enabled else None
        )
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.git_status_provider = git_status_provider or self._git_status
        self.logger = self._build_logger()

    def run_forever(self) -> None:
        while True:
            self.run_once()
            self.sleep_fn(self.check_interval_seconds)

    def run_once(self) -> AISupervisorResult:
        self._ensure_directories()
        actions: list[str] = []
        before = self._read_scan_status()

        needs_refresh = (
            before["scan_id"] is None
            or before["age_minutes"] is None
            or float(before["age_minutes"]) > float(self.refresh_if_older_than_minutes)
        )

        if needs_refresh:
            refresh = self._run_full_refresh()
            if refresh.returncode != 0:
                message = self._build_failure_message("full_refresh_failed", refresh)
                self._write_review(
                    self._local_status_note(
                        title="Gemini Supervisor: Refresh Failed",
                        lines=[
                            "전체 최신화 실행이 실패했습니다.",
                            "",
                            f"- command: `./scripts/run_full_pipeline.sh`",
                            f"- stderr: `{_truncate(refresh.stderr or refresh.stdout, 500)}`",
                        ],
                    )
                )
                self._save_state(
                    actions=["full_refresh_failed"],
                    scan_id=before["scan_id"],
                    scan_age_minutes=before["age_minutes"],
                    review_fingerprint=None,
                )
                result = AISupervisorResult(
                    ok=False,
                    actions=["full_refresh_failed"],
                    scan_id=before["scan_id"],
                    scan_age_minutes=before["age_minutes"],
                    review_written=True,
                    snapshot_written=False,
                    message=message,
                )
                self.logger.error(message)
                return result
            actions.append("full_refresh")

        after = self._read_scan_status()
        scanner = MarketActivityScanner(self.settings)
        live_phase = scanner.resolve_market_phase("auto")
        live_scan_written = False
        live_activity: list[object] = []
        if live_phase in {"premarket", "regular"}:
            try:
                comparison_csv = self.project_root / "sample_outputs" / f"{live_phase}_prediction_vs_actual.csv"
                scan_result = scanner.scan(
                    phase=live_phase,
                    top_limit=10,
                    comparison_csv=comparison_csv,
                )
                live_activity = scan_result.activity
                actions.append(f"live_market_scan:{live_phase}")
                live_scan_written = True
            except RuntimeError as exc:
                self.logger.warning("Skipping live market scan: %s", exc)

        paper_result = self._run_paper_trading(live_phase=live_phase, activity=live_activity)
        if paper_result is not None:
            paper_actions = ",".join(paper_result.actions[:4])
            actions.append(f"paper_trade:{paper_actions}")

        snapshot_written = False
        if needs_refresh or self._snapshot_needs_export() or live_scan_written:
            self.snapshot_builder(self.snapshot_output)
            actions.append("snapshot_export")
            snapshot_written = True

        payload = ReportBuilder().build_payload(self.settings.database_path, limit=10)
        git_status = self.git_status_provider(self.project_root)
        prompt_template = self._load_prompt_template()
        review_fingerprint = self._review_fingerprint(
            after,
            payload,
            git_status,
            prompt_template,
        )
        previous_state = self._load_state()

        review_written = False
        if self.reviewer is not None and (
            previous_state.get("review_fingerprint") != review_fingerprint
            or not self.review_output.exists()
        ):
            try:
                prompt = self._build_prompt(
                    after,
                    payload,
                    git_status,
                    actions,
                    prompt_template=prompt_template,
                )
                review = self.reviewer.generate_markdown(prompt)
                self._write_review(review)
                actions.append("gemini_review")
                review_written = True
            except Exception as exc:
                self._write_review(
                    self._local_status_note(
                        title="Gemini Supervisor: Review Failed",
                        lines=[
                            "Gemini 2차 리뷰 생성이 실패했습니다.",
                            "",
                            f"- error: `{_truncate(str(exc), 500)}`",
                            f"- latest_scan_id: `{after['scan_id'] or '-'}`",
                            f"- snapshot: `{self.snapshot_output}`",
                        ],
                    )
                )
                actions.append("gemini_review_failed")
                review_written = True
        elif self.reviewer is None and not self.review_output.exists():
            self._write_review(
                self._local_status_note(
                    title="Gemini Supervisor: Review Skipped",
                    lines=[
                        "Gemini 리뷰는 건너뛰었습니다.",
                        "",
                        "- reason: `PENNY_STOCK_GEMINI_API_KEY`가 설정되지 않았습니다.",
                        f"- latest_scan_id: `{after['scan_id'] or '-'}`",
                        f"- snapshot: `{self.snapshot_output}`",
                    ],
                )
            )
            actions.append("gemini_missing_api_key")
            review_written = True

        if not actions:
            actions.append("noop")

        self._save_state(
            actions=actions,
            scan_id=after["scan_id"],
            scan_age_minutes=after["age_minutes"],
            review_fingerprint=review_fingerprint,
        )
        message = (
            f"AI supervisor finished with actions={','.join(actions)} "
            f"scan_id={after['scan_id'] or '-'} age_minutes="
            f"{_format_optional_float(after['age_minutes'])}"
        )
        result = AISupervisorResult(
            ok=True,
            actions=actions,
            scan_id=after["scan_id"],
            scan_age_minutes=after["age_minutes"],
            review_written=review_written,
            snapshot_written=snapshot_written,
            message=message,
        )
        self.logger.info(message)
        return result

    def _ensure_directories(self) -> None:
        self.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        self.review_output.parent.mkdir(parents=True, exist_ok=True)
        self.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _build_logger(self) -> logging.Logger:
        logger_name = f"penny_stock_radar.ai_supervisor.{id(self)}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
        return logger

    def _build_prompt(
        self,
        status: dict[str, Any],
        payload: dict[str, object],
        git_status: str,
        actions: list[str],
        prompt_template: str | None = None,
    ) -> str:
        template_text = prompt_template or self._load_prompt_template()
        summary = self._payload_summary(payload)
        template = Template(template_text)
        return template.safe_substitute(
            run_at=self.now_fn().isoformat(),
            scan_id=str(status["scan_id"] or "-"),
            scan_age_minutes=_format_optional_float(status["age_minutes"]),
            actions=", ".join(actions) if actions else "noop",
            watchlist=summary["watchlist"],
            premarket=summary["premarket"],
            session=summary["session"],
            live_premarket=summary["live_premarket"],
            prediction_premarket=summary["prediction_premarket"],
            live_regular=summary["live_regular"],
            prediction_regular=summary["prediction_regular"],
            social=summary["social"],
            report=summary["report"],
            git_status=git_status or "clean",
            snapshot_path=str(self.snapshot_output),
            review_path=str(self.review_output),
        )

    def _load_prompt_template(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return (
            "You are the Gemini second-opinion reviewer for Penny Stock Radar.\n"
            "Time: $run_at\n"
            "Scan ID: $scan_id\n"
            "Scan age (minutes): $scan_age_minutes\n"
            "Actions this run: $actions\n"
            "Snapshot path: $snapshot_path\n"
            "Review path: $review_path\n\n"
            "Top watchlist:\n$watchlist\n\n"
            "Top premarket:\n$premarket\n\n"
            "Top live premarket movers:\n$live_premarket\n\n"
            "Premarket prediction audit:\n$prediction_premarket\n\n"
            "Top session decisions:\n$session\n\n"
            "Top live regular movers:\n$live_regular\n\n"
            "Regular prediction audit:\n$prediction_regular\n\n"
            "Top social:\n$social\n\n"
            "Replay report:\n$report\n\n"
            "Git status:\n$git_status\n\n"
            "Respond in concise Markdown with exactly these sections:\n"
            "1. Overview\n"
            "2. Risks\n"
            "3. Next Action\n"
        )

    def _payload_summary(self, payload: dict[str, object]) -> dict[str, str]:
        def _top_lines(rows: object, fields: list[str]) -> str:
            items = rows if isinstance(rows, list) else []
            if not items:
                return "- none"
            lines: list[str] = []
            for row in items[:3]:
                if not isinstance(row, dict):
                    continue
                bits = [str(row.get("symbol") or "-")]
                for field in fields:
                    value = row.get(field)
                    if value is None or value == "":
                        continue
                    bits.append(f"{field}={value}")
                lines.append("- " + ", ".join(bits))
            return "\n".join(lines) if lines else "- none"

        report_row = payload.get("report")
        report_line = "- none"
        if isinstance(report_row, dict) and report_row:
            report_line = (
                f"- label={report_row.get('label', '-')}, "
                f"expectancy={report_row.get('expectancy', '-')}, "
                f"profit_factor={report_row.get('profit_factor', '-')}, "
                f"precision_at_k={report_row.get('precision_at_k', '-')}"
            )
        return {
            "watchlist": _top_lines(payload.get("watchlist"), ["total_score", "catalyst_score"]),
            "premarket": _top_lines(payload.get("premarket"), ["quality_score", "premarket_rvol"]),
            "live_premarket": _top_lines(
                payload.get("live_premarket"),
                ["pct_rank", "volume_rank", "pct_change", "analysis_label"],
            ),
            "prediction_premarket": _top_lines(
                payload.get("prediction_premarket"),
                ["watchlist_rank", "pct_rank", "volume_rank", "outcome"],
            ),
            "session": _top_lines(payload.get("session"), ["decision", "extension_z", "mfe_pct"]),
            "live_regular": _top_lines(
                payload.get("live_regular"),
                ["pct_rank", "volume_rank", "pct_change", "analysis_label"],
            ),
            "prediction_regular": _top_lines(
                payload.get("prediction_regular"),
                ["watchlist_rank", "pct_rank", "volume_rank", "outcome"],
            ),
            "social": _top_lines(payload.get("social"), ["social_score", "mention_velocity"]),
            "report": report_line,
        }

    def _read_scan_status(self) -> dict[str, Any]:
        if not self.settings.database_path.exists():
            return {"scan_id": None, "created_at": None, "age_minutes": None}

        try:
            with sqlite3.connect(self.settings.database_path) as connection:
                row = connection.execute(
                    """
                    SELECT scan_id, created_at
                    FROM scan_runs
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error:
            return {"scan_id": None, "created_at": None, "age_minutes": None}

        if row is None:
            return {"scan_id": None, "created_at": None, "age_minutes": None}

        scan_id = str(row[0]) if row[0] is not None else None
        created_at_text = str(row[1]) if row[1] is not None else None
        if created_at_text is None:
            return {"scan_id": scan_id, "created_at": None, "age_minutes": None}

        created_at = datetime.fromisoformat(created_at_text.replace("Z", "+00:00"))
        age_minutes = (self.now_fn() - created_at).total_seconds() / 60.0
        return {
            "scan_id": scan_id,
            "created_at": created_at.isoformat(),
            "age_minutes": max(age_minutes, 0.0),
        }

    def _review_fingerprint(
        self,
        status: dict[str, Any],
        payload: dict[str, object],
        git_status: str,
        prompt_template: str,
    ) -> str:
        raw = json.dumps(
            {
                "scan_id": status["scan_id"],
                "created_at": status["created_at"],
                "git_status": git_status,
                "prompt_template_hash": hashlib.sha256(
                    prompt_template.encode("utf-8")
                ).hexdigest(),
                "watchlist": payload.get("watchlist"),
                "premarket": payload.get("premarket"),
                "live_premarket": payload.get("live_premarket"),
                "prediction_premarket": payload.get("prediction_premarket"),
                "session": payload.get("session"),
                "live_regular": payload.get("live_regular"),
                "prediction_regular": payload.get("prediction_regular"),
                "social": payload.get("social"),
                "report": payload.get("report"),
            },
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(
        self,
        actions: list[str],
        scan_id: str | None,
        scan_age_minutes: float | None,
        review_fingerprint: str | None,
    ) -> None:
        payload = {
            "updated_at": self.now_fn().isoformat(),
            "actions": actions,
            "scan_id": scan_id,
            "scan_age_minutes": scan_age_minutes,
            "review_fingerprint": review_fingerprint,
            "snapshot_output": str(self.snapshot_output),
            "review_output": str(self.review_output),
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _run_full_refresh(self) -> CommandResult:
        return self.command_runner(
            [str(self.project_root / "scripts" / "run_full_pipeline.sh")],
            self.project_root,
        )

    def _build_snapshot(self, output_path: Path) -> Path:
        return build_snapshot_dashboard(
            self.settings.database_path,
            output_path=output_path,
            limit=20,
        )

    def _run_paper_trading(
        self,
        *,
        live_phase: str,
        activity: list[object],
    ) -> object | None:
        if self.paper_trading_engine is None:
            return None
        should_run = bool(activity)
        if not should_run:
            active_run = fetch_active_paper_trading_run(
                self.settings.database_path,
                self.paper_trading_engine.strategy_name,
            )
            if active_run is not None:
                open_positions = fetch_paper_positions(
                    self.settings.database_path,
                    active_run.run_id,
                    status="OPEN",
                )
                should_run = bool(open_positions)
        if not should_run:
            return None
        try:
            return self.paper_trading_engine.process_market_activity(
                market_phase=live_phase,
                activity=list(activity),
                export_csv=True,
            )
        except Exception as exc:
            self.logger.warning("Paper trading pass failed: %s", exc)
            return None

    def _snapshot_needs_export(self) -> bool:
        if not self.snapshot_output.exists():
            return True

        latest_data_mtime = self._latest_data_mtime()
        if latest_data_mtime is None:
            return False

        try:
            return self.snapshot_output.stat().st_mtime < latest_data_mtime
        except OSError:
            return True

    def _latest_data_mtime(self) -> float | None:
        related_paths = [
            self.settings.database_path,
            self.settings.database_path.with_name(self.settings.database_path.name + "-wal"),
            self.settings.database_path.with_name(self.settings.database_path.name + "-journal"),
        ]
        mtimes: list[float] = []
        for path in related_paths:
            try:
                if path.exists():
                    mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
        if not mtimes:
            return None
        return max(mtimes)

    def _git_status(self, project_root: Path) -> str:
        result = self.command_runner(["git", "status", "--short"], project_root)
        if result.returncode != 0:
            return f"git status failed: {result.stderr or result.stdout}"
        return result.stdout.strip() or "clean"

    def _write_review(self, content: str) -> None:
        header = (
            f"<!-- generated_at: {self.now_fn().isoformat()} -->\n"
            f"<!-- snapshot: {self.snapshot_output} -->\n\n"
        )
        self.review_output.write_text(header + content.strip() + "\n", encoding="utf-8")

    def _local_status_note(self, title: str, lines: list[str]) -> str:
        return f"# {title}\n\n" + "\n".join(lines).strip() + "\n"

    def _build_failure_message(self, reason: str, result: CommandResult) -> str:
        return (
            f"AI supervisor failed ({reason}) with exit code {result.returncode}: "
            f"{_truncate(result.stderr or result.stdout, 200)}"
        )

    def _run_command(self, command: list[str], cwd: Path) -> CommandResult:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )


def build_gemini_reviewer(settings: AppSettings) -> GeminiReviewer | None:
    if not settings.gemini_api_key:
        return None
    return GeminiReviewer(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        base_url=settings.gemini_api_base_url,
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"
