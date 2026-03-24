# Penny Stock Radar

미국 penny stock을 `후보 선별 -> 프리장 검증 -> 정규장 판단` 흐름으로 보는 연구용 레이더입니다.

핵심 목적:
- 프리장 전에 볼 만한 종목 압축
- 프리장 급등이 진짜인지/가짜인지 구분
- 정규장에서 continuation인지 fade인지 판단
- 실시간 API가 있으면 최신 quote/trade/snapshot 확인

## 빠른 실행

맥:

```bash
./launch_dashboard.command
```

윈도우:

```bat
launch_dashboard.bat
```

윈도우 PowerShell:

```powershell
.\launch_dashboard.ps1
```

처음 실행 시 자동으로:
- `.venv` 생성
- 필요한 패키지 설치
- `.env` 생성
- 필요 시 전체 파이프라인 최신화
- Streamlit 대시보드 실행

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

## 대시보드에서 보는 흐름

- `전체 후보군` = 1차 필터
- `프리장 전 분석` = 오늘 볼 종목 압축
- `프리장 중 분석` = 진짜/가짜 급등 판별
- `정규장 분석` = continuation/fade 판단
- `실시간 모드` = 지금도 거래가 살아있는지 확인

## 자주 쓰는 명령

전체 파이프라인 수동 실행:

```bash
./scripts/run_full_pipeline.sh
```

대시보드 직접 실행:

```bash
./scripts/psradar dashboard
```

실시간 스냅샷 확인:

```bash
./scripts/psradar show-live-market
```

## 문서

- 판단 가이드: [`docs/judgment_guide_ko.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/judgment_guide_ko.md)
- 윈도우 가이드: [`docs/windows_setup_ko.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/windows_setup_ko.md)

## 주의

- 기본 구조는 `replay/mock-first`입니다.
- `yfinance`는 discovery/EOD 성격입니다.
- 실시간 모드는 `웹소켓 틱 스트리밍`이 아니라 `주기적 polling`입니다.
- API 키가 없으면 저장된 마지막 분석 결과를 보여줍니다.
