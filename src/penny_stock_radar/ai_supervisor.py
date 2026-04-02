from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from string import Template
import subprocess
import time
from typing import Any, Callable

import httpx

from .config import AppSettings
from .db import fetch_active_paper_trading_run, fetch_paper_positions, fetch_scan_selection
from .runtime_scripts import full_refresh_command, full_refresh_command_label
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


AUTOMATION_STATUS_FIELDS = (
    "updated_at",
    "status",
    "latest_scan_id",
    "scan_age_minutes",
    "snapshot_path",
    "snapshot_age_minutes",
    "review_path",
    "last_refresh_action",
    "last_error",
    "degraded_reason",
)
AUTOMATION_STATUS_VALUES = {"ok", "degraded", "failed", "closed_market"}
DEFAULT_AUTOMATION_STATUS_PATH = Path("automation/state/automation_status.json")
DEFAULT_AI_SUPERVISOR_STATE_PATH = Path("automation/state/ai_supervisor_state.json")
SUPERVISOR_LOG_MAX_BYTES = 512 * 1024
SUPERVISOR_LOG_BACKUP_COUNT = 5


def default_automation_status_path(project_root: Path | None = None) -> Path:
    return _resolve_path(project_root or _project_root(), DEFAULT_AUTOMATION_STATUS_PATH)


def load_automation_status(
    status_path: Path,
    *,
    now_fn: Callable[[], datetime] = _now_utc,
) -> dict[str, Any]:
    payload = _default_automation_status_payload(now_fn=now_fn)
    if not status_path.exists():
        payload["last_error"] = "Automation status file not found. Run `psradar ai-supervisor --run-once` first."
        return payload
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        payload["last_error"] = "Automation status file is unreadable."
        return payload

    if not isinstance(raw, dict):
        payload["last_error"] = "Automation status file has an invalid format."
        return payload

    for key in AUTOMATION_STATUS_FIELDS:
        payload[key] = raw.get(key)

    if payload["status"] not in AUTOMATION_STATUS_VALUES:
        payload["status"] = "failed"
        payload["last_error"] = "Automation status file has an unknown status value."

    return payload


def format_automation_status_text(payload: dict[str, Any]) -> str:
    lines = [
        f"updated_at: {payload.get('updated_at') or '-'}",
        f"status: {payload.get('status') or '-'}",
        f"latest_scan_id: {payload.get('latest_scan_id') or '-'}",
        f"scan_age_minutes: {_format_optional_float(payload.get('scan_age_minutes'))}",
        f"snapshot_path: {payload.get('snapshot_path') or '-'}",
        f"snapshot_age_minutes: {_format_optional_float(payload.get('snapshot_age_minutes'))}",
        f"review_path: {payload.get('review_path') or '-'}",
        f"last_refresh_action: {payload.get('last_refresh_action') or '-'}",
        f"last_error: {payload.get('last_error') or '-'}",
        f"degraded_reason: {payload.get('degraded_reason') or '-'}",
    ]
    return "\n".join(lines)


def _default_automation_status_payload(
    *,
    now_fn: Callable[[], datetime],
) -> dict[str, Any]:
    return {
        "updated_at": now_fn().isoformat(),
        "status": "failed",
        "latest_scan_id": None,
        "scan_age_minutes": None,
        "snapshot_path": None,
        "snapshot_age_minutes": None,
        "review_path": None,
        "last_refresh_action": None,
        "last_error": None,
        "degraded_reason": None,
    }


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


@dataclass(slots=True)
class _SupervisorRunContext:
    before: dict[str, Any]
    after: dict[str, Any]
    actions: list[str] = field(default_factory=list)
    review_written: bool = False
    snapshot_written: bool = False
    review_fingerprint: str | None = None
    last_error: str | None = None
    degraded_reasons: list[str] = field(default_factory=list)
    last_refresh_action: str = "skipped_refresh"


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
        state_path: Path = DEFAULT_AI_SUPERVISOR_STATE_PATH,
        automation_status_path: Path = DEFAULT_AUTOMATION_STATUS_PATH,
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
        self.automation_status_path = _resolve_path(self.project_root, automation_status_path)
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
        before = self._read_scan_status()
        context = _SupervisorRunContext(before=before, after=before)

        try:
            needs_refresh = self._needs_refresh(context.before)
            refresh_result = self._refresh_if_needed(context, needs_refresh=needs_refresh)
            if refresh_result is not None:
                return refresh_result

            context.after = self._read_scan_status()
            missing_scan_result = self._fail_if_no_complete_scan(context)
            if missing_scan_result is not None:
                return missing_scan_result

            live_phase, live_activity, live_scan_written, base_status = self._run_live_scan(
                context
            )

            paper_result = self._run_paper_trading(live_phase=live_phase, activity=live_activity)
            if paper_result is not None:
                paper_actions = ",".join(paper_result.actions[:4])
                context.actions.append(f"paper_trade:{paper_actions}")

            if needs_refresh or self._snapshot_needs_export() or live_scan_written:
                self.snapshot_builder(self.snapshot_output)
                context.actions.append("snapshot_export")
                context.snapshot_written = True

            self._update_review(context)

            automation_status = (
                "degraded" if context.degraded_reasons else base_status
            )
            message = (
                f"AI supervisor finished status={automation_status} "
                f"actions={','.join(context.actions or ['noop'])} "
                f"scan_id={context.after['scan_id'] or '-'} age_minutes="
                f"{_format_optional_float(context.after['age_minutes'])}"
            )
            return self._finalize_result(
                ok=True,
                actions=context.actions,
                scan_status=context.after,
                review_written=context.review_written,
                snapshot_written=context.snapshot_written,
                review_fingerprint=context.review_fingerprint,
                automation_status=automation_status,
                last_refresh_action=context.last_refresh_action,
                last_error=context.last_error,
                degraded_reason=", ".join(context.degraded_reasons) or None,
                message=message,
                log_method="info",
            )
        except Exception as exc:
            return self._handle_unexpected_failure(context, exc)

    def _needs_refresh(self, scan_status: dict[str, Any]) -> bool:
        age_minutes = scan_status.get("age_minutes")
        return (
            scan_status.get("scan_id") is None
            or age_minutes is None
            or float(age_minutes) > float(self.refresh_if_older_than_minutes)
        )

    def _refresh_if_needed(
        self,
        context: _SupervisorRunContext,
        *,
        needs_refresh: bool,
    ) -> AISupervisorResult | None:
        if not needs_refresh:
            return None

        refresh = self._run_full_refresh()
        if refresh.returncode == 0:
            context.actions.append("full_refresh")
            context.last_refresh_action = "full_refresh"
            return None

        return self._handle_refresh_failure(context, refresh)

    def _handle_refresh_failure(
        self,
        context: _SupervisorRunContext,
        refresh: CommandResult,
    ) -> AISupervisorResult:
        context.last_refresh_action = "full_refresh_failed"
        context.last_error = _truncate(refresh.stderr or refresh.stdout, 500)

        if context.before["scan_id"] is not None:
            self.snapshot_builder(self.snapshot_output)
            context.snapshot_written = True
            context.review_written = True
            context.actions.extend(["full_refresh_failed", "fallback_export"])
            context.degraded_reasons.append("refresh_failed_fallback")
            self._write_local_review(
                title="Gemini Supervisor: Refresh Failed, Fallback Export Used",
                lines=[
                    "전체 최신화 실행이 실패했지만 마지막 complete scan으로 snapshot을 다시 export했습니다.",
                    "",
                    f"- command: `{full_refresh_command_label()}`",
                    f"- stderr: `{context.last_error}`",
                    f"- selected_scan_id: `{context.before['scan_id'] or '-'}`",
                    f"- latest_raw_scan_id: `{context.before.get('latest_scan_id') or '-'}`",
                    f"- snapshot: `{self.snapshot_output}`",
                ],
            )
            message = (
                f"AI supervisor refresh failed; exported fallback snapshot from "
                f"scan_id={context.before['scan_id'] or '-'}"
            )
            return self._finalize_result(
                ok=True,
                actions=context.actions,
                scan_status=context.before,
                review_written=context.review_written,
                snapshot_written=context.snapshot_written,
                review_fingerprint=None,
                automation_status="degraded",
                last_refresh_action=context.last_refresh_action,
                last_error=context.last_error,
                degraded_reason=", ".join(context.degraded_reasons),
                message=message,
                log_method="warning",
            )

        context.review_written = True
        context.actions.append("full_refresh_failed")
        self._write_local_review(
            title="Gemini Supervisor: Refresh Failed",
            lines=[
                "전체 최신화 실행이 실패했습니다.",
                "",
                f"- command: `{full_refresh_command_label()}`",
                f"- stderr: `{context.last_error}`",
            ],
        )
        message = self._build_failure_message("full_refresh_failed", refresh)
        return self._finalize_result(
            ok=False,
            actions=context.actions,
            scan_status=context.before,
            review_written=context.review_written,
            snapshot_written=context.snapshot_written,
            review_fingerprint=None,
            automation_status="failed",
            last_refresh_action=context.last_refresh_action,
            last_error=context.last_error,
            degraded_reason=None,
            message=message,
            log_method="error",
        )

    def _fail_if_no_complete_scan(
        self,
        context: _SupervisorRunContext,
    ) -> AISupervisorResult | None:
        if context.after["scan_id"] is not None:
            return None

        context.last_error = "No complete scan is available for automation output generation."
        context.review_written = True
        context.actions.append("no_complete_scan")
        self._write_local_review(
            title="Gemini Supervisor: No Complete Scan Available",
            lines=[
                "자동화 출력에 사용할 complete scan을 찾지 못했습니다.",
                "",
                f"- latest_raw_scan_id: `{context.after.get('latest_scan_id') or '-'}`",
                f"- snapshot: `{self.snapshot_output}`",
            ],
        )
        return self._finalize_result(
            ok=False,
            actions=context.actions,
            scan_status=context.after,
            review_written=context.review_written,
            snapshot_written=context.snapshot_written,
            review_fingerprint=None,
            automation_status="failed",
            last_refresh_action=context.last_refresh_action,
            last_error=context.last_error,
            degraded_reason=None,
            message=context.last_error,
            log_method="error",
        )

    def _run_live_scan(
        self,
        context: _SupervisorRunContext,
    ) -> tuple[str, list[object], bool, str]:
        scanner = MarketActivityScanner(self.settings)
        live_phase = scanner.resolve_market_phase("auto")
        base_status = "closed_market" if live_phase not in {"premarket", "regular"} else "ok"
        if live_phase not in {"premarket", "regular"}:
            return live_phase, [], False, base_status

        try:
            comparison_csv = (
                self.project_root / "sample_outputs" / f"{live_phase}_prediction_vs_actual.csv"
            )
            scan_result = scanner.scan(
                phase=live_phase,
                top_limit=10,
                comparison_csv=comparison_csv,
            )
        except RuntimeError as exc:
            context.last_error = _truncate(str(exc), 500)
            context.degraded_reasons.append("live_market_scan_unavailable")
            self.logger.warning("Skipping live market scan: %s", exc)
            return live_phase, [], False, base_status

        context.actions.append(f"live_market_scan:{live_phase}")
        return live_phase, scan_result.activity, True, base_status

    def _update_review(self, context: _SupervisorRunContext) -> None:
        payload = ReportBuilder().build_payload(self.settings.database_path, limit=10)
        git_status = self.git_status_provider(self.project_root)
        prompt_template = self._load_prompt_template()
        context.review_fingerprint = self._review_fingerprint(
            context.after,
            payload,
            git_status,
            prompt_template,
        )
        previous_state = self._load_state()

        should_generate = self.reviewer is not None and (
            previous_state.get("review_fingerprint") != context.review_fingerprint
            or not self.review_output.exists()
        )
        if should_generate:
            self._generate_review(
                context,
                payload=payload,
                git_status=git_status,
                prompt_template=prompt_template,
            )
            return

        if self.reviewer is None and not self.review_output.exists():
            self._write_local_review(
                title="Gemini Supervisor: Review Skipped",
                lines=[
                    "Gemini 리뷰는 건너뛰었습니다.",
                    "",
                    "- reason: `PENNY_STOCK_GEMINI_API_KEY`가 설정되지 않았습니다.",
                    f"- latest_scan_id: `{context.after['scan_id'] or '-'}`",
                    f"- snapshot: `{self.snapshot_output}`",
                ],
            )
            context.actions.append("gemini_missing_api_key")
            context.review_written = True

    def _generate_review(
        self,
        context: _SupervisorRunContext,
        *,
        payload: dict[str, object],
        git_status: str,
        prompt_template: str,
    ) -> None:
        try:
            prompt = self._build_prompt(
                context.after,
                payload,
                git_status,
                context.actions,
                prompt_template=prompt_template,
            )
            review = self.reviewer.generate_markdown(prompt) if self.reviewer is not None else ""
            self._write_review(review)
            context.actions.append("gemini_review")
            context.review_written = True
        except Exception as exc:
            context.last_error = _truncate(str(exc), 500)
            context.degraded_reasons.append("gemini_review_failed")
            self._write_local_review(
                title="Gemini Supervisor: Review Failed",
                lines=[
                    "Gemini 2차 리뷰 생성이 실패했습니다.",
                    "",
                    f"- error: `{context.last_error}`",
                    f"- latest_scan_id: `{context.after['scan_id'] or '-'}`",
                    f"- snapshot: `{self.snapshot_output}`",
                ],
            )
            context.actions.append("gemini_review_failed")
            context.review_written = True

    def _handle_unexpected_failure(
        self,
        context: _SupervisorRunContext,
        exc: Exception,
    ) -> AISupervisorResult:
        context.last_error = _truncate(str(exc), 500)
        try:
            self._write_local_review(
                title="Gemini Supervisor: Unexpected Failure",
                lines=[
                    "자동화 실행 중 예상하지 못한 오류가 발생했습니다.",
                    "",
                    f"- error: `{context.last_error}`",
                    f"- latest_scan_id: `{context.after.get('scan_id') or context.before.get('scan_id') or '-'}`",
                    f"- snapshot: `{self.snapshot_output}`",
                ],
            )
            context.review_written = True
        except Exception:
            pass

        if not context.actions:
            context.actions.append("unexpected_failure")
        self.logger.exception("AI supervisor unexpected failure")
        scan_status = (
            context.after
            if context.after.get("scan_id") or context.after.get("latest_scan_id")
            else context.before
        )
        message = f"AI supervisor failed unexpectedly: {context.last_error}"
        return self._finalize_result(
            ok=False,
            actions=context.actions,
            scan_status=scan_status,
            review_written=context.review_written,
            snapshot_written=context.snapshot_written,
            review_fingerprint=context.review_fingerprint,
            automation_status="failed",
            last_refresh_action=context.last_refresh_action,
            last_error=context.last_error,
            degraded_reason=None,
            message=message,
            log_method=None,
        )

    def _ensure_directories(self) -> None:
        self.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        self.review_output.parent.mkdir(parents=True, exist_ok=True)
        self.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.automation_status_path.parent.mkdir(parents=True, exist_ok=True)

    def _build_logger(self) -> logging.Logger:
        logger_name = f"penny_stock_radar.ai_supervisor.{id(self)}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, self.settings.log_level.upper(), logging.INFO))
        logger.propagate = False
        if not logger.handlers:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self.log_path,
                maxBytes=SUPERVISOR_LOG_MAX_BYTES,
                backupCount=SUPERVISOR_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
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
        selection = fetch_scan_selection(self.settings.database_path, now=self.now_fn())
        scan_id = str(selection["selected_scan_id"]) if selection.get("selected_scan_id") else None
        created_at_text = (
            str(selection["selected_created_at"])
            if selection.get("selected_created_at")
            else None
        )
        if created_at_text is None:
            return {
                "scan_id": scan_id,
                "created_at": None,
                "age_minutes": None,
                "latest_scan_id": selection.get("latest_scan_id"),
                "latest_created_at": selection.get("latest_created_at"),
                "is_fallback": bool(selection.get("is_fallback")),
                "missing_core_sections": list(selection.get("missing_core_sections") or []),
                "missing_supplemental_sections": list(
                    selection.get("missing_supplemental_sections") or []
                ),
                "market_phase": selection.get("market_phase"),
                "live_data_expected": bool(selection.get("live_data_expected")),
            }

        created_at = datetime.fromisoformat(created_at_text.replace("Z", "+00:00"))
        age_minutes = (self.now_fn() - created_at).total_seconds() / 60.0
        return {
            "scan_id": scan_id,
            "created_at": created_at.isoformat(),
            "age_minutes": max(age_minutes, 0.0),
            "latest_scan_id": selection.get("latest_scan_id"),
            "latest_created_at": selection.get("latest_created_at"),
            "is_fallback": bool(selection.get("is_fallback")),
            "missing_core_sections": list(selection.get("missing_core_sections") or []),
            "missing_supplemental_sections": list(selection.get("missing_supplemental_sections") or []),
            "market_phase": selection.get("market_phase"),
            "live_data_expected": bool(selection.get("live_data_expected")),
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

    def _save_automation_status(
        self,
        *,
        automation_status: str,
        scan_status: dict[str, Any],
        last_refresh_action: str | None,
        last_error: str | None,
        degraded_reason: str | None,
    ) -> None:
        latest_scan_id = scan_status.get("scan_id") or scan_status.get("latest_scan_id")
        payload = {
            "updated_at": self.now_fn().isoformat(),
            "status": automation_status,
            "latest_scan_id": latest_scan_id,
            "scan_age_minutes": _round_optional_float(scan_status.get("age_minutes")),
            "snapshot_path": str(self.snapshot_output),
            "snapshot_age_minutes": _round_optional_float(
                self._path_age_minutes(self.snapshot_output)
            ),
            "review_path": str(self.review_output),
            "last_refresh_action": last_refresh_action,
            "last_error": last_error,
            "degraded_reason": degraded_reason,
        }
        self.automation_status_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _finalize_result(
        self,
        *,
        ok: bool,
        actions: list[str],
        scan_status: dict[str, Any],
        review_written: bool,
        snapshot_written: bool,
        review_fingerprint: str | None,
        automation_status: str,
        last_refresh_action: str | None,
        last_error: str | None,
        degraded_reason: str | None,
        message: str,
        log_method: str | None,
    ) -> AISupervisorResult:
        final_actions = actions or ["noop"]
        self._save_state(
            actions=final_actions,
            scan_id=scan_status.get("scan_id"),
            scan_age_minutes=scan_status.get("age_minutes"),
            review_fingerprint=review_fingerprint,
        )
        self._save_automation_status(
            automation_status=automation_status,
            scan_status=scan_status,
            last_refresh_action=last_refresh_action,
            last_error=last_error,
            degraded_reason=degraded_reason,
        )
        if log_method:
            getattr(self.logger, log_method)(message)
        return AISupervisorResult(
            ok=ok,
            actions=final_actions,
            scan_id=scan_status.get("scan_id"),
            scan_age_minutes=scan_status.get("age_minutes"),
            review_written=review_written,
            snapshot_written=snapshot_written,
            message=message,
        )

    def _run_full_refresh(self) -> CommandResult:
        return self.command_runner(full_refresh_command(self.project_root), self.project_root)

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

    def _path_age_minutes(self, path: Path) -> float | None:
        try:
            if not path.exists():
                return None
            age_seconds = self.now_fn().timestamp() - path.stat().st_mtime
        except OSError:
            return None
        return max(age_seconds / 60.0, 0.0)

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

    def _write_local_review(self, title: str, lines: list[str]) -> None:
        self._write_review(self._local_status_note(title, lines))

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


def _round_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
