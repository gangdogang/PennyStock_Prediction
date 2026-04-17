# 운영 가이드

## 빠른 실행

macOS:

```bash
./launch_dashboard.command
./launch_snapshot.command
./launch_ai_supervisor.command
./launch_paper_trader.command
```

Windows:

```powershell
.\launchers\windows\launch_dashboard.ps1
.\launchers\windows\launch_dashboard_lan.ps1
.\launchers\windows\launch_paper_trader.ps1
```

## 환경 설정

macOS:

```bash
./live_api_setup.command
```

Windows:

```powershell
.\launchers\windows\live_api_setup.ps1
```

`.env`에 아래 중 하나를 넣으면 실시간 모드가 동작합니다.

- `PENNY_STOCK_POLYGON_API_KEY`
- `PENNY_STOCK_ALPACA_API_KEY`
- `PENNY_STOCK_ALPACA_SECRET_KEY`

Gemini 2차 리뷰를 쓰려면:

- `PENNY_STOCK_GEMINI_API_KEY`

## 자주 쓰는 CLI

전체 파이프라인:

```bash
./scripts/run_full_pipeline.sh
```

스냅샷 HTML 생성:

```bash
./scripts/psradar snapshot-dashboard
```

AI supervisor 1회 실행:

```bash
./scripts/psradar ai-supervisor --run-once
```

자동화 상태:

```bash
./scripts/psradar automation-status
./scripts/psradar automation-status --format json
```

## 장시간 운영

Windows 기본 권장:

```powershell
.\launchers\windows\install_ai_supervisor_task.ps1
```

상태 확인:

```powershell
.\launchers\windows\ai_supervisor_task_status.ps1
```

macOS background paper trader:

```bash
./start_paper_trader_background.command
./paper_trader_status.command
./stop_paper_trader_background.command
```

## 산출물 위치

- 스냅샷 HTML: `sample_outputs/radar_dashboard.html`
- 모의매매 CSV: `sample_outputs/paper_trading/`
- Gemini 리뷰: `automation/inbox/gemini_review.md`
- 상태 JSON: `automation/state/automation_status.json`
- supervisor 로그: `automation/logs/`

## 정리 원칙

- `sample_outputs/` 는 결과물 폴더다. 기준 문서로 취급하지 않는다.
- `data/` 는 로컬 캐시/DB 폴더다. 오래된 DB, WAL, cache 파일은 주기적으로 비운다.
- 새 컨텍스트에 넘길 핵심 정보는 산출물이 아니라 `docs/STATUS.md`에 남긴다.
