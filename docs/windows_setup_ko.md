# Penny Stock Radar 윈도우 실행 가이드

이 문서는 `Windows 10/11`에서 `Penny Stock Radar`를 실행하는 가장 쉬운 방법을 한국어로 정리한 문서입니다.

## 1. 무엇을 쓰면 되나

윈도우에서는 두 가지 방식이 있습니다.

- `launch_dashboard.bat`
- `launch_dashboard.ps1`

API 키 설정도 두 가지가 있습니다.

- `live_api_setup.bat`
- `live_api_setup.ps1`

가장 쉬운 시작점은 보통 아래 둘입니다.

- `live_api_setup.bat`
- `launch_dashboard.bat`

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

## 7. 추천

- 처음 설치와 실행:
  - `launch_dashboard.bat`
- 실시간 API 키 편집:
  - `live_api_setup.bat`
- PowerShell에 익숙하면:
  - `.ps1` 버전 사용

## 8. 한 줄 요약

윈도우에서도 핵심 파이썬 코드는 그대로 쓰고, 실행 파일만 `bat/ps1`로 분리해서 사용하면 됩니다.
