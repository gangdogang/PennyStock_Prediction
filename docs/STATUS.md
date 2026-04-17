# Status

최종 정리일: 2026-04-17

## 현재 상태

이 프로젝트는 `watchlist -> replay/premarket analysis -> regular-session decision -> snapshot dashboard -> paper trading -> AI supervisor` 흐름까지 연결된 연구용 레이더입니다.

현재 저장소 기준으로 이미 있는 것:

- universe 구축과 DB 초기화
- SEC filing 기반 watchlist 빌드
- replay/mock 중심 premarket 및 regular-session 분석
- Streamlit 대시보드와 standalone snapshot HTML
- paper trading 루프와 결과 CSV 저장
- Windows Task Scheduler / macOS launchd 기반 supervisor 운영
- Gemini 2차 리뷰 자동화

## 기본 운영 흐름

권장 진입점:

- 사람: `README.md`
- 에이전트: `AGENTS.md`
- 현재 우선순위 파악: 이 문서

일반 실행 순서:

1. `live_api_setup` 으로 `.env` 정리
2. `launch_dashboard` 또는 `scripts/run_full_pipeline.sh`
3. 필요 시 `ai-supervisor --run-once`
4. 장시간 운용이면 Windows 작업 스케줄러 또는 macOS launchd 사용

## 현재 한계

- 실시간 모드는 polling 중심이며 full tape / websocket 트레이딩 엔진이 아니다.
- 기본 구조는 여전히 `replay/mock-first` 성격이 강하다.
- 로컬 산출물은 많이 생기지만, 저장소 기준 상태는 문서와 소스코드로 판단해야 한다.
- `sample_outputs/` 와 `data/` 는 실행 결과가 섞이기 쉬우므로 주기적으로 비우는 편이 맞다.

## 다음 우선순위

최근 작업 맥락상 우선순위는 아래 순서가 적절하다.

1. paper trading의 실전성 보강
2. live market 데이터 품질과 halt/condition 처리 강화
3. 리스크 제어와 수수료/슬리피지 모델 현실화
4. 자동화 결과를 사람이 더 빨리 읽을 수 있게 요약 개선

## 문서 운영 규칙

- milestone식 과거 기록은 유지하지 않는다.
- 현재 상태가 바뀌면 이 문서를 우선 갱신한다.
- 실행법이 바뀌면 `README.md`와 `docs/OPERATIONS_KO.md`를 함께 맞춘다.
