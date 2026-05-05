# Agent Guide

이 저장소에서 새 컨텍스트의 에이전트는 기본 문서만 먼저 읽고, 작업 종류가 확정된 뒤 필요한 문서를 추가로 엽니다.

1. [`README.md`](/Users/wondokyeong/Desktop/Penny_Stock/README.md)
2. [`docs/STATUS.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STATUS.md)
3. [`docs/STEP_PROGRESS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/STEP_PROGRESS_KO.md)

작업별 추가 문서:

- 백테스트, replay, 검증, 성능평가: [`docs/BACKTEST_ROADMAP_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/BACKTEST_ROADMAP_KO.md)
- 실행, Windows 서버, OneDrive, 런처, `.env`: [`docs/OPERATIONS_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/OPERATIONS_KO.md)
- 매매 해석, 전략 판단 기준: [`docs/TRADING_GUIDE_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/TRADING_GUIDE_KO.md)
- 엔진 분리 작업: [`docs/ENGINE_SPLIT_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/ENGINE_SPLIT_PLAN_KO.md)
- live/shadow 전환 판단: [`docs/LIVE_TRADING_READINESS_PLAN_KO.md`](/Users/wondokyeong/Desktop/Penny_Stock/docs/LIVE_TRADING_READINESS_PLAN_KO.md)

기본으로 읽지 않는 영역:

- `archive/`: 완료된 계획, 과거 리뷰, 전환 전 보관본
- `sample_outputs/`, `data/`, `automation/inbox/`, `automation/logs/`, `automation/state/`: 런타임 산출물
- `automation/prompts/`: AI supervisor 프롬프트를 수정할 때만 읽는다.

## 목적

- 이 저장소는 연구/운영 겸용이다.
- 문서는 `현재 상태`, `운영 방법`, `판단 기준`만 짧게 유지한다.
- 런타임 산출물보다 추적 가능한 기준 문서를 우선한다.

## 작업 원칙

- 작은 작업 전에 먼저 현재 구조와 관련 파일을 읽고 시작한다.
- 병렬화 가능한 조사나 구현은 토큰 비용보다 작업 속도와 분리된 책임을 우선해 서브에이전트를 적극적으로 활용한다.
- 진행 상황이나 우선순위가 바뀌면 `docs/STATUS.md`를 먼저 갱신한다.
- 사용하지 않는 산출물, 캐시, 로그, 중복 문서는 남기지 않는다.
- `sample_outputs/`, `data/`, `automation/inbox/`, `automation/logs/`, `automation/state/` 는 기본적으로 런타임 영역이다.
- 별도 handoff 문서는 유지하지 않고 이 문서와 `README.md`를 진입 기준으로 사용한다.
- 새 에이전트가 매번 모든 `.md` 를 읽어야 하는 구조를 만들지 않는다. 핵심 상태는 `README.md`, `docs/STATUS.md`, `docs/STEP_PROGRESS_KO.md` 에서 끝나야 한다.

## 문서 규칙

- `README.md`: 사람이 바로 실행하고 구조를 이해하는 진입점
- `docs/STATUS.md`: 현재 capabilities, 제약, 다음 우선순위
- `docs/BACKTEST_ROADMAP_KO.md`: 백테스트/검증 로드맵의 기준 문서
- `docs/STEP_PROGRESS_KO.md`: Step 단위 진행 현황과 현재 작업 규칙
- `docs/OPERATIONS_KO.md`: 실행/운영/runbook
- `docs/TRADING_GUIDE_KO.md`: 시장 해석과 매매 판단 기준
- `archive/`: 완료된 계획 문서와 전환 전 보관본

새 문서를 추가하기 전에 위 기준 문서 중 하나에 흡수할 수 있는지 먼저 본다.
