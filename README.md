# Penny Stock Radar

미국 급등주를 `후보 선별 -> 프리장 검증 -> 정규장 판단 -> 모의매매` 흐름으로 보는 연구용 레이더입니다.

이 저장소는 두 가지 목적에 맞춰 정리되어 있습니다.

- 사람이 바로 실행하고 운영할 수 있을 것
- 새 컨텍스트의 에이전트가 문서 몇 개만 읽고 현재 상태를 빠르게 파악할 수 있을 것

운영 역할은 맥북에서 코드 수정/테스트/commit/push 를 하고, Windows 머신은 `C:\Dev\Penny_Stock` 기준 24시간 paper/backtest 서버로 돌리는 방식입니다.

## 시작점

macOS 대시보드:

```bash
./launchers/macos/launch_dashboard.command
```

macOS 스냅샷 UI:

```bash
./launchers/macos/launch_snapshot.command
```

macOS AI supervisor:

```bash
./launchers/macos/launch_ai_supervisor.command
```

Windows 대시보드:

```powershell
.\launchers\windows\launch_dashboard.ps1
```

Windows paper 실행/스냅샷:

```powershell
.\launchers\windows\run_paper_drive.ps1
.\launchers\windows\archive_paper_run_snapshot.ps1
```

API 키 설정:

```bash
./launchers/macos/live_api_setup.command
```

```powershell
.\launchers\windows\live_api_setup.ps1
```

## 문서 읽기 순서

새 컨텍스트의 기본 읽기 범위:

- 에이전트 작업 규칙: [`AGENTS.md`](/Users/wondokyeong/Desktop/Penny_Stock/AGENTS.md)
- 현재 상태와 다음 우선순위: [`docs/STATUS.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STATUS.md)
- Step 단위 작업 규칙: [`docs/STEP_PROGRESS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STEP_PROGRESS_KO.md)

작업별로 필요할 때만 추가로 읽는 문서:

- 백테스트/replay/성능평가: [`docs/BACKTEST_ROADMAP_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/BACKTEST_ROADMAP_KO.md)
- Windows 서버/OneDrive/런처/.env: [`docs/OPERATIONS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/OPERATIONS_KO.md)
- 매매/판단 기준: [`docs/TRADING_GUIDE_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/TRADING_GUIDE_KO.md)
- 엔진 분리: [`docs/ENGINE_SPLIT_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/ENGINE_SPLIT_PLAN_KO.md)
- live/shadow 전환: [`docs/LIVE_TRADING_READINESS_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/LIVE_TRADING_READINESS_PLAN_KO.md)

`archive/`, `sample_outputs/`, `automation/inbox/` 아래 markdown 은 기본 진입 문서가 아니라 과거 기록 또는 런타임 산출물입니다.

## 폴더 구조

- 루트: 핵심 설정, 핵심 문서, 패키지 메타
- `src/penny_stock_radar/`: 애플리케이션 코드
- `archive/`: 전환 전 보관본, 완료된 계획 문서, 작업 스냅샷
- `tests/`: 테스트
- `scripts/`: 공통 CLI/pipeline/품질 게이트 스크립트
- `launchers/macos/`: macOS Finder-friendly `.command` 런처
- `launchers/windows/`: Windows 대화형 런처, OneDrive paper 실행/스냅샷 런처, 작업 스케줄러 스크립트
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

Phase 0 falsification data coverage audit:

```bash
./scripts/psradar audit-research-data-coverage --run-id coverage_$(date +%Y%m%d) --strategy-run-dir data/backtest_lab/replays/<run_id> --strategy-bucket predictor_weighted
```

Hugging Face CryptoSpartan 1m bars audit:

```bash
PSR_DATA_ROOT=/path/to/Penny_Stock_Data ./scripts/psradar audit-hf-1m-bars --run-id hf_$(date +%Y%m%d)
```

Windows:

```powershell
$env:PSR_DATA_ROOT="C:\Dev\Penny_Stock_Data"
.\scripts\psradar.ps1 audit-hf-1m-bars --run-id hf_20260507
```

이 감사는 gross OHLCV falsification data 용도만 지원하며 산출물은 `decision_grade=false`, `cost_grade=none` 이다. parquet 는 repo 로 복사하지 않는다.

Hugging Face candidate-day segmentation:

```bash
PSR_DATA_ROOT=/path/to/Penny_Stock_Data ./scripts/psradar segment-hf-candidate-days --run-id hf_candidates_$(date +%Y%m%d)
```

Windows:

```powershell
$env:PSR_DATA_ROOT="C:\Dev\Penny_Stock_Data"
.\scripts\psradar.ps1 segment-hf-candidate-days --run-id hf_candidates_20260507
```

Windows 메모리가 부족하면 더 작은 date chunk 로 실행한다.

```powershell
.\scripts\psradar.ps1 segment-hf-candidate-days --run-id hf_candidates_20260507 --chunk-months 1
```

이 명령은 1분봉 parquet 를 ticker-day 단위로 분해해 `candidate_days.csv` 와 gate summary 를 만든다. `PASS` 는 gross candidate-day coverage 가 충분하다는 뜻일 뿐 setup backtest, cost/fill, live 가능 판정이 아니다.

Hugging Face candidate-event segmentation:

```bash
PSR_DATA_ROOT=/path/to/Penny_Stock_Data ./scripts/psradar segment-hf-candidate-events --run-id hf_events_$(date +%Y%m%d)
```

Windows:

```powershell
$env:PSR_DATA_ROOT="C:\Dev\Penny_Stock_Data"
.\scripts\psradar.ps1 segment-hf-candidate-events --run-id hf_events_20260507 --chunk-months 1
```

이 명령은 09:45/10:30/14:00/15:30 ET event-time 후보를 만들고, 후보 생성에는 event 시점까지의 OHLCV 만 사용한다. event 시점 최신 bar 가 기본 2분보다 오래됐으면 stale 로 제외한다. 이후 30/60/120분 regular-session gross return/max-up/max-down 은 결과 진단 컬럼일 뿐이며 산출물은 `decision_grade=false`, `cost_grade=none` 이다.

Hugging Face candidate-event robustness audit:

```powershell
$env:PSR_DATA_ROOT="C:\Dev\Penny_Stock_Data"
.\scripts\psradar.ps1 audit-hf-candidate-event-robustness --run-id hf_event_robustness_20260507
```

이 명령은 연도별 `candidate_events.csv` 를 합쳐 top1/top5/top10 ticker 제거 전후 event 수, event-time/time-bucket 분포, forward outcome 변화를 감사한다. `PASS` 는 top ticker 집중 착시가 약하다는 최소 조건일 뿐이며 setup backtest, cost/fill, live 가능 판정이 아니다.

Hugging Face event random benchmark:

```powershell
$env:PSR_DATA_ROOT="C:\Dev\Penny_Stock_Data"
.\scripts\psradar.ps1 run-hf-event-random-benchmark --run-id hf_event_random_20260507
```

이 명령은 같은 ticker/day 안에서 deterministic random event-time 을 만들고 HF parquet 로 동일한 30/60/120분 forward outcome 을 계산해 candidate event 와 비교한다. 기본 gate 는 `ex_top_10` cohort 의 120분 `same_time_bucket` random 대비 mean/median/win-rate 우위이며, 산출물은 계속 `decision_grade=false`, `cost_grade=none` 이다.

Coverage shortfall planning:

```bash
./scripts/psradar report-coverage-shortfall --target-minute-bars-months 6 --target-cost-eligible-overlap-pct 80 --target-corporate-action-months 12 --vendor-quote-source databento_nbbo --vendor-quote-cost-per-month-usd 99 --out automation/state/shortfall/$(date +%Y%m%d_%H%M%S).json
```

Universe KIS tradability audit:

```bash
./scripts/psradar audit-universe-tradability --universe-source replay_log --replay-dir data/backtest_lab/replays/<run_id> --out-dir automation/state/tradability/<run_id>
```

무료 데이터 blocker 보강 CLI:

```bash
./scripts/psradar backfill-alpaca-iex-quotes --market-date 2026-05-01 --symbol ABCD --start-time 09:25 --end-time 10:00
./scripts/psradar archive-nasdaq-symbol-directory --output-dir data/backtest_lab/reference_snapshots/nasdaq_symbol_directory_$(date +%Y%m%d)
./scripts/psradar backfill-sec-filings-pit --start-date 2025-06-02 --end-date 2025-06-30 --symbol ABCD --form 8-K --cutoff-time 08:00
./scripts/psradar backfill-finra-otc-daily-list --run-id finra_otc_$(date +%Y%m%d) --limit 1000
```

Alpaca IEX 는 diagnostic-only 이며 NBBO/SIP cost evidence 가 아니다. Nasdaq Symbol Directory 는 forward PIT archive 이고 과거 PIT 복원이 아니다. 상세 Windows runbook 은 `docs/OPERATIONS_KO.md` 의 "Phase 0 무료 데이터 MVP runbook" 을 본다.

IBKR historical NBBO backfill:

```bash
./scripts/psradar backfill-ibkr-historical-quotes --symbols-file watchlist.txt --market-date-start 2025-12-01 --market-date-end 2026-05-01 --paper-account --out-summary automation/state/ibkr_backfill/$(date +%Y%m%d_%H%M%S).json
```

IBKR 경로는 optional extra 이므로 실행 머신에 `pip install .[ibkr]` 가 필요하다. 적재 source 는 `ibkr_nbbo` 이고 license 는 personal use / redistribution 금지로 등록한다.

Replay entry-signal audit:

```bash
./scripts/psradar audit-premkt-entry-signal --run-dir data/replay_outputs/no_conditional_june --csv-dir data/replay_outputs/entry_signal_audit
```

Setup alert diagnostic export:

```bash
./scripts/psradar build-setup-alerts-from-features --features-csv data/backtest_lab/replays/<run_id>/paper_setup_features.csv
```

이 명령은 `AfternoonVwapReclaim`, `Day2MorningPanic`, `FirstGreenDayContinuation` 후보/blocked 사유를 setup alert 로 분리한다. 주문을 내거나 setup backtest 를 수행하지 않으며 산출물은 `decision_grade=false`, `cost_grade=none` 이다.

Falsification-first overnight audit:

```bash
./scripts/psradar run-falsification-audit --run-id overnight_$(date +%Y%m%d)
```

목적: 이 명령은 feature tuning 전 필수 반증 게이트다. preflight, 산출물, pass/fail 판단은 `docs/OPERATIONS_KO.md` 의 "Overnight falsification runbook" 과 `docs/BACKTEST_ROADMAP_KO.md` 의 falsification gate 를 기준으로 본다. 이 gate 가 `PASS` 되기 전에는 entry/setup/score/filter/stop/sizing tuning 을 하지 않는다.

Strategy trade-log matched null:

```bash
./scripts/psradar run-falsification-audit --run-id matched_$(date +%Y%m%d) --strategy-trade-log <paper_trade_log.csv> --strategy-bucket predictor_weighted
```

`same_universe_random_entry` 는 실제 strategy entry timing 을 유지하고 exact PIT universe 안에서 replacement symbol 을 뽑는 null benchmark 다. exact PIT, 같은 분봉 bar overlap, cost sample 이 없으면 blocked 로 남긴다.

Benchmark suite report:

```bash
./scripts/psradar run-benchmark-suite --run-id benchmark_$(date +%Y%m%d) --strategy-trade-log <paper_trade_log.csv> --strategy-bucket predictor_weighted
```

이 명령은 6개 benchmark entry-event/report 배선만 준비한다. cost-eligible source 가 없으면 benchmark generation 도 blocked 로 남긴다.

PIT universe reconstruction audit:

```bash
./scripts/psradar audit-pit-universe-reconstruction --run-id pit_audit_$(date +%Y%m%d)
```

이 명령은 exact point-in-time universe 와 bar-derived diagnostic universe 가능성을 분리한다. diagnostic universe 는 edge 판단 blocker 를 해소하지 않는다.

Explicit PIT scan tagging:

```bash
./scripts/psradar tag-pit-universe-scan --scan-id <scan_id> --market-date YYYY-MM-DD
```

기존 scan 을 PIT 로 태그할 때만 사용한다. D 08:00 ET 이후 scan 은 기본 거부된다.

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
- 새 작업을 시작할 때는 `AGENTS.md -> README.md -> docs/STATUS.md -> docs/STEP_PROGRESS_KO.md` 순서로 보는 것이 가장 빠릅니다. 백테스트 작업일 때만 `docs/BACKTEST_ROADMAP_KO.md` 를 추가로 봅니다.
