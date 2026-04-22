# Penny Stock Radar

미국 급등주를 `후보 선별 -> 프리장 검증 -> 정규장 판단 -> 모의매매` 흐름으로 보는 연구용 레이더입니다.

이 저장소는 두 가지 목적에 맞춰 정리되어 있습니다.

- 사람이 바로 실행하고 운영할 수 있을 것
- 새 컨텍스트의 에이전트가 문서 몇 개만 읽고 현재 상태를 빠르게 파악할 수 있을 것

## 시작점

macOS 대시보드:

```bash
./launch_dashboard.command
```

macOS 스냅샷 UI:

```bash
./launch_snapshot.command
```

macOS AI supervisor:

```bash
./launch_ai_supervisor.command
```

Windows 대시보드:

```powershell
.\launchers\windows\launch_dashboard.ps1
```

Windows 24시간 paper 성능평가:

```powershell
.\launchers\windows\run_paper_24h_drive.ps1
```

API 키 설정:

```bash
./live_api_setup.command
```

```powershell
.\launchers\windows\live_api_setup.ps1
```

## 먼저 읽을 문서

- 에이전트 작업 규칙: [`AGENTS.md`](/Users/wondokyeong/Desktop/Penny_Stock/AGENTS.md)
- 현재 진행 상태: [`docs/STATUS.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STATUS.md)
- 백테스트 기준 로드맵: [`docs/BACKTEST_ROADMAP_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/BACKTEST_ROADMAP_KO.md)
- 단계별 진행 기록: [`docs/STEP_PROGRESS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STEP_PROGRESS_KO.md)
- 운영 가이드: [`docs/OPERATIONS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/OPERATIONS_KO.md)
- 매매/판단 기준: [`docs/TRADING_GUIDE_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/TRADING_GUIDE_KO.md)
- 2엔진 전환 설계: [`docs/ENGINE_SPLIT_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/ENGINE_SPLIT_PLAN_KO.md)
- 실매매 준비 계획: [`docs/LIVE_TRADING_READINESS_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/LIVE_TRADING_READINESS_PLAN_KO.md)

## 폴더 구조

- 루트: macOS `.command` 런처, 핵심 설정, 핵심 문서
- `src/penny_stock_radar/`: 애플리케이션 코드
- `archive/`: 전환 전 보관본, 완료된 계획 문서, 작업 스냅샷
- `tests/`: 테스트
- `scripts/`: 공통 CLI/pipeline/품질 게이트 스크립트
- `launchers/windows/`: Windows 대화형 런처, Drive 성능평가 런처, 작업 스케줄러 스크립트
- `automation/`: supervisor 프롬프트, launchd 템플릿, 상태/로그 폴더
- `docs/`: 장기적으로 유지할 핵심 문서
- `data/`: 로컬 DB, 캐시, replay 입력
- `sample_outputs/`: 대시보드 HTML, CSV, 요약 리포트 같은 런타임 산출물

런처 분류 기준은 [`launchers/README.md`](/Users/wondokyeong/Desktop/Penny_Stock/launchers/README.md)에 둔다.

## 자주 쓰는 명령

전체 파이프라인:

```bash
./scripts/run_full_pipeline.sh
```

대시보드 직접 실행:

```bash
./scripts/psradar dashboard
```

스냅샷 HTML 생성:

```bash
./scripts/psradar snapshot-dashboard
```

AI supervisor 1회 실행:

```bash
./scripts/psradar ai-supervisor --run-once
```

프리장 예측기 1회 실행:

```bash
./scripts/psradar run-premkt-predictor
```

KIS minute backfill:

```bash
./scripts/psradar backfill-kis-minute --market-date 2026-04-17
```

KIS L1 snapshot archive:

```bash
./scripts/psradar capture-kis-l1 --symbol TSLA --symbol SOUN
```

KIS L1 archive window:

```bash
./scripts/psradar capture-kis-l1-window --iterations 10 --interval-seconds 60
```

Backtest L1 coverage report:

```bash
./scripts/psradar report-backtest-coverage --market-date 2026-04-17 --session premarket
```

품질 게이트 실행:

```bash
./scripts/check_quality.sh
```

`--strict-coverage-gate` 는 지원하지만 Step 0 coverage 60% 확보 전까지는 켜지 않는다.

자동화 상태 확인:

```bash
./scripts/psradar automation-status
```

## 런타임 산출물

- 스냅샷 HTML: `sample_outputs/radar_dashboard.html`
- 모의매매 CSV: `sample_outputs/paper_trading/`
- Gemini 리뷰: `automation/inbox/gemini_review.md`
- 자동화 상태: `automation/state/automation_status.json`
- 자동화 로그: `automation/logs/`

위 파일들은 실행 중 생성되는 로컬 산출물이며, 저장소의 기준 문서는 아닙니다.

## 주의

- 기본 구조는 `replay/mock-first` 입니다.
- `yfinance`는 discovery/EOD 성격입니다.
- 실시간 모드는 웹소켓 기반 전체 틱 엔진이 아니라 주기적 polling 중심입니다.
- 현재는 `intraday` 엔진과 `multiday` 엔진을 분리하는 전환 단계입니다.
- 기존 intraday 핵심 파일 복사본은 `archive/engine_split_2026-04-18/` 에 보관되어 있습니다.
- 새 작업을 시작할 때는 `AGENTS.md -> README.md -> docs/STATUS.md -> docs/BACKTEST_ROADMAP_KO.md` 순서로 보는 것이 가장 빠릅니다.
