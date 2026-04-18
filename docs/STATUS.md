# Status

최종 정리일: 2026-04-19

## 현재 capabilities

- `universe -> watchlist -> premarket 분석 -> regular-session 판단` 기본 흐름이 로컬 DB 기준으로 동작한다.
- SEC filing 기반 watchlist 빌드와 `filed_at <= D 08:00 ET` cutoff 적용이 가능하다.
- point-in-time universe snapshot 태깅과 `market_date` 기준 재현 경로가 있다.
- `PremktPredictor` 가 후보 점수, 추천 보유일, 진입 근거를 DB/JSON 으로 저장할 수 있다.
- intraday paper engine 이 stale/halt/daily-loss/open-risk 가드레일과 함께 replay/mock-first 실행을 수행한다.
- multiday engine 이 starter, overnight hold, winner add, loser replacement, day2/day3 exit 1차 규칙을 수행한다.
- multiday engine 의 starter/add/hold/replacement 파라미터가 `AppSettings` 와 `.env` override 로 제어되며 기본값 기준 골든 스냅샷이 유지된다.
- KIS live quote timestamp 는 shared helper 기준으로 ET/KST fallback 정규화가 적용되고 `live_market.py` 와 `kis_historical.py` 가 같은 해석 경로를 사용한다.
- live scan/provider 경로는 `automation/logs/live_metrics.jsonl` 기준 JSONL sidecar 로 `quote_age`, `spread`, provider request reject 신호, scan summary 를 남긴다.
- `report-backtest-coverage` 는 coverage report latest JSON 을 `automation/state/backtest_coverage/` 기준으로 정기 산출할 수 있다.
- `capture-kis-l1-window` 는 같은 심볼 집합으로 반복 L1 snapshot 적재를 수행해 session interval coverage archive 를 누적할 수 있다.
- Step 0 L1 coverage gate 상태는 `automation/state/backtest_coverage_gate_status.json` latest snapshot 으로 저장되고, 상태 파일의 pass/fail 규칙은 고정되며 CI hard fail 은 Step 0 60% 도달 후 켠다.
- 골든 회귀 기준은 intraday `stop_loss`/`time_stop`, multiday `day2_exit`/`overnight_hold_rejected`/`loser_replacement`, fill-model stop gap 경로까지 고정돼 있다.
- 로컬 품질 게이트는 `scripts/check_quality.sh`, `scripts/check_quality.ps1` 기준으로 골든 회귀 + 전체 `pytest` + coverage gate 상태 요약을 같은 진입점에서 실행할 수 있다.
- GitHub Actions CI 는 `.github/workflows/ci.yml` 기준으로 `.[dev,ui]` 설치 후 같은 품질 게이트를 실행한다.
- intraday 와 multiday 의 `process_market_activity` 가 `services/engine_shared.py:run_step()` 공통 오케스트레이터를 사용한다.
- `services/engine_rules/entry.py`, `exit.py`, `profit.py` 로 intraday 규칙이 분리됐고 multiday도 같은 exit/close 경계를 재사용한다.
- DB 계층은 `db/connection.py`, `schema.py`, `paper.py`, `execution.py`, `historical.py`, `premkt.py` 중심 패키지로 분할됐고 기존 `from ..db import ...` import 경로는 유지된다.
- CLI 는 `cli/__init__.py` 루트 앱 아래 `premkt.py`, `backtest.py`, `automation.py`, `paper.py`, `broker.py`와 보조 서브앱으로 분할됐고 기존 `psradar <cmd>` 명령 표면은 유지된다.
- `predictor_weighted` 와 `momentum_only` 버킷을 독립 포트폴리오로 병렬 비교할 수 있다.
- paper trading 결과는 snapshots, orders, positions, KPI CSV, execution quality CSV 로 남길 수 있다.
- KIS historical minute backfill, L1 snapshot archive, coverage report CLI 가 연결돼 있다.
- KIS mock broker execution 경로가 `providers/broker.py`, `providers/kis_mock_broker.py`, `services/broker_execution.py` 기준으로 분리돼 있다.
- broker execution 결과는 `execution_orders`, `execution_positions`, `execution_accounts` 테이블에 저장된다.
- Streamlit 대시보드, snapshot HTML, AI supervisor, launcher 스크립트가 같은 저장소 구조를 기준으로 동작한다.

## 현재 한계

- `BACKTEST_ROADMAP_KO.md` Step 0 기준 KIS historical minute/L1 coverage 60% gate 는 아직 통과하지 못했다.
- 임의의 과거 날짜 D 전체를 재현할 만큼 장기 archive 적재량이 아직 부족하다.
- live observability 는 아직 JSONL sidecar 수준이며 대시보드 집계, 알람, broker execution reject telemetry 분리는 남아 있다.
- stale/halt/trade-condition hard gate 는 live smoke 기준으로 다시 검증해야 한다.
- `report_builder.py`, `ai_supervisor.py`, `providers/live_market.py` 는 여전히 단일 파일이 커서 변경 범위가 넓다.
- KIS mock broker execution 은 `trade-plan` 기반 반자동 검증 범위만 지원하고 auto loop, reconciliation, recovery runbook 이 없다.
- full tape/websocket 기반 실시간 엔진이 아니며 기본 구조는 계속 `replay/mock-first` 성격이 강하다.
- Step 4/5 리포트는 존재하지만 Step 0 coverage 와 shadow/out-of-sample 검증 전에는 live 판단 근거가 될 수 없다.

## 다음 우선순위

- 현재 제품 기준 최우선은 `BACKTEST_ROADMAP_KO.md` Step 0 완료다.
- 이를 위해 `backfill-kis-minute`, `capture-kis-l1`, `capture-kis-l1-window`, `report-backtest-coverage` 경로로 coverage 를 계속 채우고 gate 통과 여부를 추적한다.
- 현재 저장소 정리 작업의 Step 1~10은 완료됐고, 다음 우선순위는 Step 0 coverage 60% gate 확보와 archive 적재다.
- 그 다음에는 shadow 모드와 out-of-sample 검증으로 넘어간다.
- Step 6은 완료됐고 multiday 설정은 `AppSettings` 와 `.env.example` 로 승격됐으며 env override 회귀 테스트와 골든 검증을 통과했다.
- Step 7은 완료됐고 KIS live timestamp 정규화와 live JSONL observability 추가 후 전체 `173 passed` 를 확인했다.
- Step 8은 완료됐고 coverage report latest JSON 과 gate 상태 파일이 기준 경로에 고정됐으며 전체 `176 passed` 를 확인했다.
- Step 9는 완료됐고 intraday `time_stop`, multiday `overnight_hold_rejected`/`loser_replacement` 골든 스냅샷을 추가한 뒤 전체 `179 passed` 를 확인했다.
- Step 10은 완료됐고 로컬 품질 게이트 스크립트와 GitHub Actions CI 를 추가한 뒤 전체 `184 passed` 를 확인했다.
- Step 0 보강으로 `capture-kis-l1-window` 반복 archive runner 를 추가했고 관련/전체 테스트 `185 passed` 를 확인했다.
- 이후에는 Step 0 coverage 확보와 shadow/out-of-sample 검증 순으로 다시 돌아간다.
- live readiness Phase 1 은 timestamp/observability 기반은 들어왔지만 live smoke 와 hard gate 검증 전까지는 여전히 문서/검증 단계로 유지한다.
- 골든 스냅샷과 `tests/golden/` 은 의도된 diff 가 아니면 건드리지 않는다.
- 각 Step 완료 시 이 문서를 먼저 갱신하고, 그 다음 진행 기록과 관련 문서를 맞춘다.
