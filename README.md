# Penny Stock Radar

미국 급등주를 `후보 선별 -> 프리장 검증 -> 정규장 판단 -> 모의매매` 흐름으로 보는 연구용 레이더입니다.

핵심 목적:
- 프리장 전에 볼 만한 종목 압축
- 프리장 급등이 진짜인지/가짜인지 구분
- 정규장에서 continuation인지 fade인지 판단
- 페니스탁이 아니어도 당일 심리에 불이 붙는 급등주 추적
- 실시간 API가 있으면 최신 quote/trade/snapshot 확인

## 빠른 실행

맥:

```bash
./launch_dashboard.command
```

URL 서버 없이 스냅샷 UI만 바로 열기:

```bash
./launch_snapshot.command
```

Codex 자동화와 함께 로컬 Gemini 사이드카까지 계속 돌리기:

```bash
./launch_ai_supervisor.command
```

24시간 감시형 모의투자 엔진만 따로 돌리기:

```bash
./launch_paper_trader.command
```

터미널을 닫아도 계속 도는 백그라운드 LaunchAgent 켜기:

```bash
./start_paper_trader_background.command
```

백그라운드 LaunchAgent 끄기:

```bash
./stop_paper_trader_background.command
```

백그라운드 LaunchAgent 상태 확인:

```bash
./paper_trader_status.command
```

윈도우:

```bat
launch_dashboard.bat
```

윈도우 PowerShell:

```powershell
.\launch_dashboard.ps1
```

윈도우 데스크탑에서 맥으로 접속 가능한 LAN 대시보드 열기:

```powershell
.\launch_dashboard_lan.ps1
```

윈도우 데스크탑에서 24시간 모의투자 루프 실행:

```powershell
.\launch_paper_trader.ps1
```

윈도우 로그인 시 자동 시작되는 24시간 모니터링 + 리포트 supervisor 등록:

```powershell
.\install_ai_supervisor_task.ps1
```

윈도우 supervisor 작업 상태 확인:

```powershell
.\ai_supervisor_task_status.ps1
```

윈도우 supervisor 작업 제거:

```powershell
.\remove_ai_supervisor_task.ps1
```

윈도우 로그인 시 자동 시작되도록 작업 스케줄러 등록:

```powershell
.\install_paper_trader_task.ps1
```

윈도우 작업 스케줄러 상태 확인:

```powershell
.\paper_trader_task_status.ps1
```

윈도우 작업 스케줄러 제거:

```powershell
.\remove_paper_trader_task.ps1
```

처음 실행 시 자동으로:
- `.venv` 생성
- 필요한 패키지 설치
- `.env` 생성
- 필요 시 전체 파이프라인 최신화
- Streamlit 대시보드 실행

윈도우 데스크탑을 24시간 켜두는 경우:
- `install_ai_supervisor_task.ps1` 로 `15분 주기 모니터링 + snapshot/review 갱신` 자동 시작 등록
- `ai_supervisor_task_status.ps1` 로 상태 JSON, stdout/stderr, 최근 실행 시간 확인
- `launch_dashboard_lan.ps1` 는 필요할 때만 수동 점검용으로 사용
- `install_paper_trader_task.ps1` 는 선택 기능으로만 사용
- 절전/최대 절전은 꺼 두고, Mac에서는 공유 폴더나 LAN 경로에서 결과 파일만 확인

## API 키 설정

맥:

```bash
./live_api_setup.command
```

윈도우:

```bat
live_api_setup.bat
```

윈도우 PowerShell:

```powershell
.\live_api_setup.ps1
```

`.env`에 `Alpaca` 또는 `Polygon` 키를 넣으면 실시간 모드가 동작합니다.

`.env`에 `PENNY_STOCK_GEMINI_API_KEY`를 넣으면 로컬 AI supervisor가 Gemini 2차 리뷰를 함께 남깁니다.

## 대시보드에서 보는 흐름

- `전체 후보군` = 1차 필터
- `프리장 전 분석` = 오늘 볼 종목 압축
- `프리장 중 분석` = replay 신호 + 저장된 실시간 top movers 비교
- `정규장 분석` = continuation/fade 판단 + 저장된 실시간 top movers 비교
- `실시간 모드` = 지금도 거래가 살아있는지, 그리고 예측이 맞았는지 확인

## 자주 쓰는 명령

전체 파이프라인 수동 실행:

```bash
./scripts/run_full_pipeline.sh
```

대시보드 직접 실행:

```bash
./scripts/psradar dashboard
```

독립형 HTML 스냅샷 UI 생성:

```bash
./scripts/psradar export-dashboard-html
```

독립형 HTML 스냅샷 UI 생성 후 바로 열기:

```bash
./scripts/psradar snapshot-dashboard
```

로컬 Gemini AI supervisor 실행:

```bash
./scripts/psradar ai-supervisor
```

모의투자 엔진 한 번 실행:

```bash
./scripts/psradar run-paper-trading
```

모의투자 엔진을 60초 polling으로 계속 실행:

```bash
./scripts/psradar paper-trader
```

최신 모의투자 손익/주문 로그 보기:

```bash
./scripts/psradar show-paper-summary
```

한 번만 점검하고 종료:

```bash
./scripts/psradar ai-supervisor --run-once
```

현재 자동화 상태 확인:

```bash
./scripts/psradar automation-status
```

자동화 상태 JSON 확인:

```bash
./scripts/psradar automation-status --format json
```

생성된 파일:

```text
sample_outputs/radar_dashboard.html
```

이 파일은 로컬 URL 서버를 띄우지 않고 브라우저나 Finder에서 바로 열 수 있습니다.

실시간 스냅샷 확인:

```bash
./scripts/psradar show-live-market
```

실시간 penny-stock movers 랭킹 + 예측 비교 CSV 생성:

```bash
./scripts/psradar scan-market-activity --phase auto
```

생성되는 비교 CSV:

```text
sample_outputs/premarket_prediction_vs_actual.csv
sample_outputs/regular_prediction_vs_actual.csv
```

모의투자 CSV 로그:

```text
sample_outputs/paper_trading/paper_run_summary.csv
sample_outputs/paper_trading/paper_positions.csv
sample_outputs/paper_trading/paper_orders.csv
sample_outputs/paper_trading/paper_equity_curve.csv
sample_outputs/paper_trading/paper_strategy_comparison.csv
```

## 로컬 AI 자동화

- `Codex` 쪽은 앱 자동화가 워크스페이스를 주기적으로 점검합니다.
- `Gemini` 쪽은 로컬 supervisor가 `automation/inbox/gemini_review.md`에 2차 의견을 남깁니다.
- paper trader는 `PENNY_STOCK_MARKET_SCOPE=all`일 때 전체 시장 급등주를 보고, Gemini가 신규 진입 후보에 구조화된 JSON 분석을 남깁니다.
- 기본 구조는 `1차 Gemini Flash` 후 `애매한 상위 1개만 Gemini Pro 재심사`입니다.
- 급등주 평가는 `무조건 1위 추격`이 아니라 `상위 5위 리더군`, `잠깐 식었다가 다시 치고 올라오는 재점화`, `눌림 흡수`, `가짜 돌파 경보`까지 함께 봅니다.
- paper trader는 `Adaptive`, `상승률 리더 baseline`, `거래량 리더 baseline`을 같이 기록해 모의손익 비교 CSV를 남깁니다.
- supervisor 로그는 [`automation/logs/ai_supervisor.log`](/Users/wondokyeong/Desktop/Penny_Stock/automation/logs/ai_supervisor.log) 에 쌓입니다.
- 공개 상태 파일은 [`automation/state/automation_status.json`](/Users/wondokyeong/Desktop/Penny_Stock/automation/state/automation_status.json) 에 저장됩니다.
- 내부 중복 방지 상태는 [`automation/state/ai_supervisor_state.json`](/Users/wondokyeong/Desktop/Penny_Stock/automation/state/ai_supervisor_state.json) 에 저장됩니다.
- Gemini 프롬프트 템플릿은 [`automation/prompts/gemini_reviewer.md`](/Users/wondokyeong/Desktop/Penny_Stock/automation/prompts/gemini_reviewer.md) 입니다.

Windows 24시간 기본 운영 경로:

- `install_ai_supervisor_task.ps1` 로 `Penny Stock Radar Supervisor` 작업 등록
- 작업은 로그인 시 1회 즉시 실행되고 이후 15분마다 `ai-supervisor --run-once --refresh-if-older-than-minutes 15` 를 호출
- 기본 산출물은 `sample_outputs/radar_dashboard.html`, `automation/inbox/gemini_review.md`, `automation/state/automation_status.json`
- 상태가 `failed` 면 먼저 `gemini_review.md`, 그 다음 Windows stderr 로그, 마지막으로 `automation_status.json` 순서로 확인

macOS 로그인 시 자동 시작까지 원하면 launchd 템플릿:

- [`automation/launchd/com.penny_stock_radar.ai_supervisor.plist`](/Users/wondokyeong/Desktop/Penny_Stock/automation/launchd/com.penny_stock_radar.ai_supervisor.plist)
- [`automation/launchd/com.penny_stock_radar.paper_trader.plist`](/Users/wondokyeong/Desktop/Penny_Stock/automation/launchd/com.penny_stock_radar.paper_trader.plist)

## 문서

- 판단 가이드: [`docs/judgment_guide_ko.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/judgment_guide_ko.md)
- 페니스탁 진입 기준: [`docs/penny_stock_entry_framework_ko.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/penny_stock_entry_framework_ko.md)
- 윈도우 가이드: [`docs/windows_setup_ko.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/windows_setup_ko.md)

## 주의

- 기본 구조는 `replay/mock-first`입니다.
- `yfinance`는 discovery/EOD 성격입니다.
- 실시간 모드는 `웹소켓 틱 스트리밍`이 아니라 `주기적 polling`입니다.
- API 키가 없으면 저장된 마지막 분석 결과를 보여줍니다.
