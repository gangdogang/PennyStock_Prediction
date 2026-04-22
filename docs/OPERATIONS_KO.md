# 운영 가이드

## 빠른 실행

macOS:

```bash
./launch_dashboard.command
./launch_snapshot.command
./launch_ai_supervisor.command
./launch_paper_trader.command
./stop_paper_trader_background.command
```

`launch_paper_trader.command` 는 내부 paper engine 전용이다. KIS mock broker execution 은 별도 CLI 로 수동/세미오토 검증한다.

Windows:

```powershell
.\launchers\windows\launch_dashboard.ps1
.\launchers\windows\launch_dashboard_lan.ps1
.\launchers\windows\launch_paper_trader.ps1
.\launchers\windows\run_paper_24h_drive.ps1
```

Windows `launch_paper_trader.ps1` 도 내부 paper engine 전용이다. 장시간 성능평가 산출물을 남길 때는 `run_paper_24h_drive.ps1` 를 우선 사용한다.

## Windows 24시간 paper 성능평가

Windows 에서 장시간 실행하고 맥북에서 검토할 때는 root `sample_outputs/paper_trading/` 을 직접 동기화하지 않는다. 아래 런처를 사용한다.

```powershell
.\launchers\windows\run_paper_24h_drive.ps1
```

기본 동작:

- Drive/OneDrive 후보 경로 아래 `paper_24h_runs\<run_id>\` 를 만든다.
- paper CSV 는 `paper_trading\`, 초기 파이프라인 샘플 산출물은 `pipeline_outputs\`, 로그는 `logs\`, zip 은 `archives\<run_id>-paper-performance.zip` 에 남긴다.
- 실행 중 SQLite DB 는 기본적으로 `data\paper_24h_runs\<run_id>\penny_stock_radar.sqlite3` 로 분리하고, 종료 시 Drive run 폴더의 `database\` 로 사본을 복사한다.
- 종료 또는 실패 시에도 `archive-paper-performance --allow-fail` 로 전송용 zip 을 만든다.
- `launcher_manifest.json` 에 run id, 경로, exit code 를 기록한다.

Drive 위치를 직접 지정하려면:

```powershell
.\launchers\windows\run_paper_24h_drive.ps1 -DriveRoot "G:\My Drive\Penny_Stock"
```

짧은 smoke 실행:

```powershell
.\launchers\windows\run_paper_24h_drive.ps1 -RunId smoke -MaxRuntimeSeconds 300 -CheckIntervalSeconds 30
```

맥북에서는 zip 을 받은 뒤 압축을 풀고 먼저 gate 를 확인한다.

```bash
./scripts/psradar review-paper-performance --export-dir <압축해제>/paper_trading
```

Step 0 L1/minute coverage 60% gate 통과 전까지는 이 결과가 좋아 보여도 live 판단 근거로 쓰지 않는다.

## 환경 설정

macOS:

```bash
./live_api_setup.command
```

Windows:

```powershell
.\launchers\windows\live_api_setup.ps1
```

`.env`에서 시세 공급자 설정과 broker execution 설정을 분리해서 본다.

- 실시간 시세 공급자: KIS 권장
  - `PENNY_STOCK_LIVE_MARKET_PROVIDER=kis`
  - `PENNY_STOCK_KIS_APP_KEY`
  - `PENNY_STOCK_KIS_APP_SECRET`
  - `PENNY_STOCK_KIS_NASDAQ_MASTER_PATH`
  - `PENNY_STOCK_KIS_NYSE_MASTER_PATH`
  - `PENNY_STOCK_KIS_AMEX_MASTER_PATH`
- 실시간 시세 공급자: KIS 해외주식 시세 API 단일 경로. `PENNY_STOCK_LIVE_MARKET_PROVIDER=kis` 가 기본값이며, 키 미설정 시 앱 기동 시 오류.
- KIS mock broker execution
  - `PENNY_STOCK_BROKER_ADAPTER=kis_mock`
  - `PENNY_STOCK_KIS_MOCK_APP_KEY`
  - `PENNY_STOCK_KIS_MOCK_APP_SECRET`
  - `PENNY_STOCK_KIS_MOCK_ACCOUNT_NUMBER`
  - `PENNY_STOCK_KIS_MOCK_ACCOUNT_PRODUCT_CODE`
  - optional:
  - `PENNY_STOCK_KIS_MOCK_BASE_URL`
  - `PENNY_STOCK_KIS_MOCK_CONTACT_PHONE`
  - `PENNY_STOCK_KIS_MOCK_ORDER_SERVER_CODE`
  - `PENNY_STOCK_KIS_MOCK_ORDER_TYPE`
  - `PENNY_STOCK_KIS_MOCK_BALANCE_EXCHANGE_CODE`
  - `PENNY_STOCK_KIS_MOCK_BALANCE_CURRENCY_CODE`
  - `PENNY_STOCK_KIS_MOCK_INQUIRY_EXCHANGE_CODE`

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

프리장 예측기 저장:

```bash
./scripts/psradar run-premkt-predictor
./scripts/psradar show-premkt-predictions
```

paper engine 실행:

```bash
./scripts/psradar run-paper-trading
./scripts/psradar show-paper-summary
./scripts/psradar archive-paper-performance --output-path sample_outputs/paper_trading_review.zip --allow-fail
```

윈도우 장시간 실행 결과를 맥북에서 검토할 때는 위의 `run_paper_24h_drive.ps1` 런처가 만든 zip 을 기준으로 본다. 수동 실행 시에도 실행 창마다 `PENNY_STOCK_PAPER_TRADE_DIR` 를 별도 폴더로 지정하고, 종료 후 `archive-paper-performance` 로 zip 을 만든 뒤 맥북에서 `review-paper-performance --export-dir <압축해제>/paper_trading` 으로 gate 를 먼저 확인한다.

KIS mock broker execution:

```bash
./scripts/psradar trade-plan
./scripts/psradar broker-submit-candidate --symbol TSLA
./scripts/psradar broker-show-orders
./scripts/psradar broker-show-fills --market-date 20260418
./scripts/psradar broker-show-balance
./scripts/psradar broker-compare-paper
```

현재 KIS mock broker 는 `daytime-order` / `daytime-order-rvsecncl` endpoint 만 사용한다.
미 동부 정규장(09:30~16:00 ET) 호출은 warning + `ValueError` 로 차단되며, 프리/애프터 세션 수동 검증 전용으로 본다.

Step 0 L1 archive 적재:

```bash
./scripts/psradar capture-kis-l1 --symbol TSLA --symbol SOUN
./scripts/psradar capture-kis-l1-window --iterations 10 --interval-seconds 60
./scripts/psradar report-backtest-coverage --market-date 2026-04-17 --session premarket
```

`capture-kis-l1-window` 는 iteration 마다 stderr 로 `iteration=<i> symbols=<N> new_rows=<N> distinct_minutes=<N>` 진단 라인을 남긴다.
`snapshot_date mismatch`, `duplicate minute bucket`, `stale timestamp fallback` 은 0건이 아닐 때만 별도 한 줄로 출력된다.

수동 주문/정정/취소:

```bash
./scripts/psradar broker-submit-order --symbol TSLA --side buy --quantity 1 --limit-price 195
./scripts/psradar broker-replace-order --client-order-id <id> --limit-price 194.5
./scripts/psradar broker-cancel-order --client-order-id <id>
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

위 background launcher 는 paper engine 전용이다. KIS mock broker execution 은 현재 background daemon 이 아니라 CLI 기반 세미오토 운영 범위만 지원한다.

## 산출물 위치

- 스냅샷 HTML: `sample_outputs/radar_dashboard.html`
- 모의매매 CSV: `sample_outputs/paper_trading/`
- KIS mock broker 상태: SQLite `execution_orders`, `execution_positions`, `execution_accounts`
- Gemini 리뷰: `automation/inbox/gemini_review.md`
- 상태 JSON: `automation/state/automation_status.json`
- supervisor 로그: `automation/logs/`

## 정리 원칙

- `sample_outputs/` 는 결과물 폴더다. 기준 문서로 취급하지 않는다.
- `data/` 는 로컬 캐시/DB 폴더다. 오래된 DB, WAL, cache 파일은 주기적으로 비운다.
- 새 컨텍스트에 넘길 핵심 정보는 산출물이 아니라 `docs/STATUS.md`에 남긴다.
