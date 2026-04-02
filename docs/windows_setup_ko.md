# Penny Stock Radar 윈도우 실행 가이드

이 문서는 `Windows 10/11`에서 `Penny Stock Radar`를 실행하는 가장 쉬운 방법을 한국어로 정리한 문서입니다.

## 1. 무엇을 쓰면 되나

윈도우에서는 기본 대시보드 실행용으로 두 가지 방식이 있습니다.

- `launch_dashboard.bat`
- `launch_dashboard.ps1`

API 키 설정도 두 가지가 있습니다.

- `live_api_setup.bat`
- `live_api_setup.ps1`

가장 쉬운 시작점은 보통 아래 둘입니다.

- `live_api_setup.bat`
- `launch_dashboard.bat`

24시간 데스크탑 운용에 바로 쓰는 추가 파일:

- `install_ai_supervisor_task.ps1`
- `ai_supervisor_task_status.ps1`
- `remove_ai_supervisor_task.ps1`
- `launch_paper_trader.bat`
- `launch_paper_trader.ps1`
- `install_paper_trader_task.bat`
- `install_paper_trader_task.ps1`
- `paper_trader_task_status.bat`
- `paper_trader_task_status.ps1`
- `remove_paper_trader_task.bat`
- `remove_paper_trader_task.ps1`
- `launch_dashboard_lan.bat`
- `launch_dashboard_lan.ps1`

## 2. 가장 쉬운 실행 순서

1. Python 3.11 이상 설치
2. 프로젝트 폴더 열기
3. 필요하면 `live_api_setup.bat` 실행해서 `.env`에 API 키 입력
4. `launch_dashboard.bat` 실행

그러면 아래를 자동으로 처리합니다.

- `.venv` 생성
- 패키지 설치
- `.env` 생성
- 최근 데이터가 없으면 전체 파이프라인 실행
- Streamlit 대시보드 실행

## 3. PowerShell 버전을 쓰고 싶다면

아래 파일도 사용할 수 있습니다.

- `live_api_setup.ps1`
- `launch_dashboard.ps1`
- `scripts/run_full_pipeline.ps1`

실행 예시:

```powershell
cd C:\path\to\Penny_Stock
.\launch_dashboard.ps1
```

## 4. PowerShell 실행 정책 때문에 막히면

PowerShell에서는 실행 정책 때문에 `.ps1` 실행이 막힐 수 있습니다.

가장 흔한 해결 방법:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

그 다음 PowerShell을 다시 열고:

```powershell
cd C:\path\to\Penny_Stock
.\launch_dashboard.ps1
```

회사 PC나 관리형 PC라면 정책 변경이 막힐 수 있으니, 그런 경우에는 `.bat` 파일을 먼저 쓰는 편이 더 쉽습니다.

## 5. API 키 설정

Alpaca 예시:

```env
PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca
PENNY_STOCK_ALPACA_API_KEY=여기에_키
PENNY_STOCK_ALPACA_SECRET_KEY=여기에_시크릿
PENNY_STOCK_ALPACA_MARKET_DATA_FEED=iex
```

Polygon 예시:

```env
PENNY_STOCK_LIVE_MARKET_PROVIDER=polygon
PENNY_STOCK_POLYGON_API_KEY=여기에_키
```

## 6. 최신화는 어떻게 하나

- `launch_dashboard.bat` 또는 `launch_dashboard.ps1`
  - 최근 15분 내 데이터가 없으면 시작 전에 전체 최신화를 자동 실행
- 대시보드 안 `전체 최신화 실행`
  - 강제로 전체 파이프라인 다시 실행

## 7. 24시간 데스크탑으로 모의투자 돌리기

기본 권장 경로는 `paper trader`가 아니라 `monitoring + report supervisor` 입니다.

가장 먼저 추천하는 설정:

```powershell
cd C:\path\to\Penny_Stock
.\install_ai_supervisor_task.ps1
```

이 작업은 아래를 수행합니다.

- 로그인 직후 `ai-supervisor --run-once` 1회 실행
- 이후 15분마다 자동 재실행
- `sample_outputs\radar_dashboard.html` 갱신
- `automation\inbox\gemini_review.md` 갱신
- `automation\state\automation_status.json` 갱신

상태 확인:

```powershell
cd C:\path\to\Penny_Stock
.\ai_supervisor_task_status.ps1
```

해제:

```powershell
cd C:\path\to\Penny_Stock
.\remove_ai_supervisor_task.ps1
```

이 경로가 24시간 운영의 기본값입니다.

## 8. paper trader는 선택 기능

가장 단순한 실행:

```powershell
cd C:\path\to\Penny_Stock
.\launch_paper_trader.ps1
```

그러면 60초 간격으로 `paper-trader` 루프가 계속 돌고, 결과 CSV는 `sample_outputs\paper_trading` 아래에 쌓입니다.

로그인할 때마다 자동으로 켜지게 만들려면:

```powershell
cd C:\path\to\Penny_Stock
.\install_paper_trader_task.ps1
```

상태 확인:

```powershell
cd C:\path\to\Penny_Stock
.\paper_trader_task_status.ps1
```

자동 시작 해제:

```powershell
cd C:\path\to\Penny_Stock
.\remove_paper_trader_task.ps1
```

이 작업 스케줄러 방식은 `현재 로그인한 윈도우 사용자` 기준입니다. 데스크탑이 켜져 있고 해당 계정으로 로그인되어 있으면 계속 돌리는 용도로 쓰기 좋습니다.

기본 운영 설명에서는 paper trader를 필수로 두지 않습니다. 모니터링/리포트가 안정화된 뒤에 선택적으로 추가하는 쪽이 좋습니다.

## 9. 맥에서 데스크탑 결과 보기

윈도우 데스크탑에서 아래를 실행합니다.

```powershell
cd C:\path\to\Penny_Stock
.\launch_dashboard_lan.ps1
```

그러면 대시보드가 `0.0.0.0:8501` 으로 열리고, 스크립트가 같은 네트워크에서 접속 가능한 `http://IPv4주소:8501` 목록을 보여줍니다.

맥에서는 브라우저로:

```text
http://데스크탑IP:8501
```

에 접속하면 됩니다.

주의:

- 맥과 데스크탑이 같은 네트워크에 있어야 합니다.
- 윈도우 방화벽이 `8501` 포트를 막고 있으면 접속이 안 될 수 있습니다.
- 기본 `launch_dashboard.bat/.ps1` 는 `localhost` 전용이고, 맥에서 보려면 `launch_dashboard_lan.*` 를 써야 합니다.
- 상시 운영에서는 Streamlit 서버를 계속 띄워두기보다, 생성된 `radar_dashboard.html` 과 `gemini_review.md` 를 공유 폴더로 보는 쪽이 더 단순합니다.

공유 폴더로 확인할 기본 파일:

- `sample_outputs\radar_dashboard.html`
- `automation\inbox\gemini_review.md`
- `automation\state\automation_status.json`

상태가 `failed` 로 보이면 아래 순서로 확인하면 됩니다.

1. `automation\inbox\gemini_review.md`
2. AI supervisor stderr 로그
3. `automation\state\automation_status.json`

## 10. 추천

- 처음 설치와 실행:
  - `launch_dashboard.bat`
- 실시간 API 키 편집:
  - `live_api_setup.bat`
- 24시간 모니터링 + 리포트:
  - `install_ai_supervisor_task.ps1`
- 선택형 24시간 모의투자:
  - `install_paper_trader_task.ps1`
- 맥에서 원격 보기:
  - `launch_dashboard_lan.ps1`
- PowerShell에 익숙하면:
  - `.ps1` 버전 사용

## 11. 절전/최대 절전

24시간 자동화를 쓰려면 Windows에서 절전과 최대 절전을 꺼 두는 편이 안전합니다.

- 전원 옵션에서 `절전 안 함`
- 가능하면 `디스플레이만 끄기` 로 운영
- 재부팅 후 자동 로그인 또는 실제 로그인 상태 유지

## 12. 한 줄 요약

윈도우 데스크탑을 계속 켜둘 수 있다면, 먼저 `install_ai_supervisor_task.ps1` 로 모니터링/리포트 자동화를 올리고, 맥에서는 생성된 HTML/Markdown/JSON 파일만 확인하는 식으로 운영하는 것이 가장 안정적입니다.
