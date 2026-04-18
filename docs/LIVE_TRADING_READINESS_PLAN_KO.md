# 실매매 전환 설계 (Live Trading Readiness)

이 문서는 현재 `penny_stock_radar` 를 **실제 주문이 나가는 시스템**으로 끌어올리기 위해 남아 있는 갭을 정리하고, 단계별(Phase) 체크리스트로 쪼갠 설계 문서다.

한 번에 다 만들 필요 없다. 각 Phase 끝에 `Exit Criteria` 가 달려 있고, 그 기준을 통과해야 다음 Phase 로 넘어간다.

---

## 0. 지금 무엇이 있고 무엇이 없는가

### 이미 있는 것
- `universe -> watchlist -> premarket/regular 분석 -> snapshot -> paper trading -> AI supervisor` 파이프라인
- `LiveMarketProvider` 추상화 (`kis` / `alpaca` / `null`)
- KIS 미국주식 master 파서 + `price / price-detail / asking-price / ranking` 정규화
- `PaperTradingEngine`, `IntradayTradingEngine`, `MultidayTradingEngine` (초기)
- stale quote / halt / spread / daily-loss-lock / open-risk-cap 가드레일 (paper 레벨)
- Streamlit 대시보드, 스냅샷 HTML, CSV export, launchd / Task Scheduler 자동화

### 실매매 기준으로 **없는 것 (gap)**
1. **브로커 주문 실행 계층** — 현재 모든 체결은 `cash_balance -= notional` 로 끝나는 가상 체결. KIS 해외주식 주문 엔드포인트 연동이 없다.
2. **주문 수명 상태머신** — `PENDING / WORKING / PARTIAL / FILLED / REJECTED / CANCELED / EXPIRED` 구분이 없다.
3. **사전 리스크 체크(Pre-Trade Risk)** — paper 전용 가드레일은 있으나 "실주문 직전" 에 독립적으로 다시 검사하는 계층이 없다.
4. **포지션/잔고 reconciliation** — 로컬 DB 와 브로커 계좌 상태를 대조하고 차이를 복구하는 루프가 없다.
5. **체결/주문 idempotency** — submit 경로는 `symbol + phase + bucket + market_date` 기반 deterministic `client_order_id` 와 explicit duplicate reject 로 막고 있다. 다만 재시작/재시도까지 포함한 영속 큐는 아직 없다.
6. **실시간 데이터 품질 수준** — 현재는 polling 중심, KIS overseas-stock `dymd/dhms` 는 ET 해석 (2026-04-18 archive 기반 경험적 확정, canary 로 drift 감지). 스프레드/호가 깊이를 주문 직전에 재확인하지 않는다.
7. **trade condition / abnormal print 필터** — 문서에도 "얕다" 고 명시되어 있음.
8. **Halt / LULD / 거래 중지 의미론** — `halt_suspected` 는 휴리스틱, 공식 halt feed 와 연결 안 됨.
9. **Kill-switch / 운영 제어판** — 긴급 정지, 전체 청산, 신규 진입 중단 등을 한 번에 트리거하는 장치 없음.
10. **감사 로그** — 의사결정 입력(스냅샷) / 결정 / 주문 / 체결 / 사후 PnL 을 하나의 trace_id 로 묶는 audit trail 부재.
11. **live 데이터 기반 shadow 벤치마크** — 실데이터로 paper 를 돌려 실매매 전환 직전 gating 하는 공식 절차가 없음.
12. **모니터링 / 알람** — 현재는 CSV, HTML, log 파일이 전부. 주문 reject rate, 데이터 staleness, 슬리피지 이상치 등을 감시하지 않음.
13. **운영 복구 런북(실매매 기준)** — 프로세스 크래시 시 진행 중 주문 처리, 토큰 만료, 시차/NTP, 세션 전환 배아웃 절차 없음.
14. **규제/계좌 제약 반영** — KIS 해외주식 주문 사전 약관, 주문 가능 시간(프리장/정규장/애프터), 미국 PDT/마진 없는 현금계좌 가정 등이 코드에 고정되어 있지 않음.
15. **성과 측정 체계 재정비** — paper 결과(slippage 임의값, 유동성 무제한 가정) 기반 KPI 는 실매매 의사결정에 쓰기 부족.

---

## 전환 원칙

이 순서를 지킨다. 지키지 않으면 돈을 잃는다.

1. **데이터 먼저, 주문은 맨 마지막.** 실데이터 품질이 충분히 검증되기 전까지 실주문 금지.
2. **Shadow → Tiny → Scale.** 실주문은 처음에 아주 작은 고정 사이즈로, 그리고 KPI gate 를 통과해야 사이즈를 올린다.
3. **Paper 와 Live 를 같은 엔진으로.** 실행 계층(`BrokerAdapter`)만 swap. 로직 분기를 만들지 않는다.
4. **모든 결정은 재현 가능해야 한다.** 입력 스냅샷 + 결정 + 주문 결과를 하나의 `trace_id` 로 묶는다.
5. **가드레일은 독립 레이어.** 엔진 내부 가드레일 + `RiskGate` 의 2중 검증.
6. **실패 기본값은 "진입 없음".** 데이터가 애매하면 신규 진입 금지, 기존 포지션은 청산 우선.

---

## Phase 1 — 데이터/관측 품질 베이스라인 (Observe-only)

목표: **실주문을 넣기 훨씬 전에**, 실데이터로 돌리는 paper 결과가 믿을 만한지 먼저 증명한다.

### 1.1 실데이터 smoke 프로토콜
- KIS 계정으로 `premarket / regular / postmarket` 각각 최소 N회 수동 smoke 돌린다.
- 저장 항목: provider, symbol, 서버시각, 브로커시각, last/bid/ask, spread, market_status, 호가 깊이, data_age_seconds, halt 여부.
- 결과는 `data/smoke/` 가 아닌 `docs/` 외부 DB/CSV 로 남기고, PR 에 요약만 첨부.

### 1.2 quote timestamp 정규화
- [`providers/live_market.py`](src/penny_stock_radar/providers/live_market.py) 의 KIS overseas-stock `dymd/dhms` 는 ET 해석 (2026-04-18 archive 기반 경험적 확정, canary 로 drift 감지) 으로 유지한다.
- 규칙: 브로커 응답의 시/분/초 + 날짜 필드를 ET(미국 동부)로 환산한 뒤 UTC 저장. 브로커가 날짜를 안 주면 `UTC now` 를 기준으로 KST clock 을 역산.
- 추가 테스트: pre-open, open, halt-resume, 종가 직전 4케이스.

### 1.3 stale / halt / trade-condition 하드 게이트
- `services/trading_support.py` 의 `halt_suspected` 를 broker 가 주는 halt/pause status 와 합쳐 **hard block**.
- `trade_condition` / `print_condition` 플래그 (KIS 에 해당하는 필드 확인 후 매핑) 들어오면 해당 프린트는 last_price 계산에서 제외.
- stale 기준은 **phase 별 분리**: premarket 15s, regular 5s, post 30s 같은 식으로. 설정키는 `AppSettings` 확장.

### 1.4 관측성 최소 세트
- 새 파일: `src/penny_stock_radar/observability/metrics.py` (혹은 기존 log 모듈 확장).
- 수집: quote age histogram, spread histogram, provider reject rate, scan duration, decision count per label.
- 로컬 파일 기반 JSONL 이면 충분. Prometheus 는 Phase 5 에서.

### Exit Criteria
- [ ] 실데이터 smoke 3세션 이상 이상 없이 기록 저장
- [ ] quote timestamp 단위 테스트 전부 통과 + 장중 smoke 에서 age 분포 정상
- [ ] halt/stale hard gate 로 paper 진입이 실제로 차단되는 케이스 한 번 이상 관찰
- [ ] `docs/STATUS.md` 의 "현재 한계" 중 타임스탬프/halt 관련 항목 제거

---

## Phase 2 — 실행 계층 추상화 (`BrokerAdapter`)

목표: **아직 실주문은 안 나간다.** 하지만 실주문을 넣을 수 있는 구조를 만든다.

### 2.1 인터페이스 정의
- 현재 기준 모듈: `src/penny_stock_radar/providers/broker.py`
- 실행 오케스트레이션 연결: `src/penny_stock_radar/services/broker_execution.py`
- 핵심 Protocol:
  ```python
  class BrokerAdapter(Protocol):
      def submit_order(self, req: OrderRequest) -> OrderAck: ...
      def cancel_order(self, broker_order_id: str) -> CancelAck: ...
      def fetch_order(self, broker_order_id: str) -> OrderState: ...
      def fetch_positions(self) -> list[BrokerPosition]: ...
      def fetch_account(self) -> AccountState: ...
  ```
- `OrderRequest` 는 반드시 `client_order_id` (idempotency key) 포함.

### 2.2 구현체 세 개
- `PaperBrokerAdapter` — 현재 paper 로직을 이 인터페이스 뒤로 옮긴다. 기존 동작 동일.
- `KisBrokerAdapter` — 실제 KIS 해외주식 주문 엔드포인트. **Phase 2 시점에선 `dry_run=True` 로 로깅만**.
- `ReplayBrokerAdapter` — 백테스트/재현용. 결정-체결 사이의 시간차를 주입.

### 2.3 엔진 교체
- [`PaperTradingEngine`](src/penny_stock_radar/services/paper_trading.py) 의 `_close_position`, `_apply_entry_rules` 내부의 "가상 체결" 블록을 `self.broker.submit_order(...)` 로 위임.
- fill 값은 `OrderAck/OrderState` 에서 가져온 값으로 대체.

### 2.4 주문 영속화
- 신규 테이블: `execution_orders` — `client_order_id (PK)`, `trace_id`, `broker_order_id`, `state`, `submitted_at`, `last_update_at`, 원본 request/response JSON.
- `insert_paper_orders` 와 별개로 유지. paper/live 모두 이 테이블에 남는다.

### Exit Criteria
- [ ] PaperBrokerAdapter 로 기존 paper 결과가 100% 재현됨 (회귀 테스트)
- [ ] KIS adapter 가 `dry_run=True` 에서 실제 호출 없이 요청 payload 로그를 남김
- [x] `client_order_id` 중복 주입 시 2번째 요청이 reject 되는 단위 테스트
- [ ] 재시작해도 `execution_orders` 가 그대로 복원

---

## Phase 3 — 리스크 게이트 & 주문 수명 상태머신

목표: 엔진 로직과 **독립된** 최종 방어선을 만든다.

### 3.1 `RiskGate` 레이어
- 현재 관련 로직 위치: `src/penny_stock_radar/services/trading_support.py`, `src/penny_stock_radar/services/trade_plan.py`
- 목표 위치: `src/penny_stock_radar/execution/risk_gate.py` 로 분리 신설
- 엔진은 `OrderRequest` 를 만들고, **반드시** `RiskGate.check(request, context)` 를 통과해야 broker 호출로 간다.
- 최소 룰:
  - 일일 손실 한도 (equity 대비 X%)
  - 동시 오픈 리스크 cap (기존 `trade_plan_max_concurrent_open_risk_pct` 재사용)
  - per-symbol 최대 수량 / 최대 notional
  - spread_pct, data_age_seconds, market_status hard gate 재검사
  - `killswitch` 파일/flag 존재 시 모든 신규 진입 차단 (청산은 허용)
  - 주문 가능 시간창(세션별) 검사

### 3.2 OrderLifecycle
- 상태: `PENDING_SUBMIT -> WORKING -> PARTIAL -> FILLED` / `REJECTED` / `CANCELED` / `EXPIRED`.
- reducer 하나로 구현: `apply_broker_event(state, event) -> state`.
- `fetch_order` 주기 polling + 브로커 webhook(있다면) 둘 다 같은 reducer 로 흡수.

### 3.3 Reconciliation
- 주기 작업: N초마다
  1. 로컬 `execution_orders` 중 `WORKING/PARTIAL` 인 것들 전부 `fetch_order`.
  2. 브로커 포지션 vs 로컬 `paper_positions` 대조. 차이 나면 알람 + 우선 중립화(신규 진입 정지).
- 이 루프가 실패하면 killswitch 자동 발동.

### Exit Criteria
- [ ] RiskGate 단위 테스트: 각 룰별로 "통과/차단" 시나리오
- [ ] OrderLifecycle 시뮬레이션 테스트: partial → cancel, reject, expire
- [ ] reconciliation mismatch 시나리오 테스트(브로커가 포지션 0, 로컬이 1 → killswitch)
- [ ] killswitch 파일 토글로 신규 진입만 차단되는 것 장중 검증

---

## Phase 4 — Shadow Live (실데이터 + 실주문 금지)

목표: **실데이터를 실시간으로 받아서 paper 로 체결**. 실주문 아직 금지. 슬리피지 모델을 실측치로 캘리브레이션.

### 4.1 Shadow 실행 모드
- 새 설정: `PENNY_STOCK_EXECUTION_MODE = shadow | paper | live`.
- `shadow` 는 broker adapter 를 `ShadowBrokerAdapter` 로 고정 — `submit_order` 는 LIVE quote + latency 모델로 paper 체결을 만든다.
- 현재 `paper_fill_slippage_pct` 같은 상수 대신, **실측 spread + age 기반 모델** 을 쓴다.

### 4.2 슬리피지 캘리브레이션
- shadow 결과에서 `fill_reference_price` vs 직후 N초 VWAP 비교 → 실제 슬리피지 분포 저장.
- Phase 4 종료 시 상수를 분포 기반으로 교체.

### 4.3 "실주문이었다면?" 보고서
- 매일 EOD 에 shadow run 의 모든 `OrderAck` 를 취합하여 "이 주문이 진짜였으면 어떤 결과였는지" 를 요약.
- 실전 전환 gate: 최소 10 거래일 / 최소 N trade, 실현 PnL 이 replay/EOD 백테스트 대비 기대치의 ±X% 이내.

### Exit Criteria
- [ ] 10 거래일 이상 shadow 가동, 엔진 크래시 없음
- [ ] 슬리피지 분포가 paper 상수 대비 현실적으로 보정되고 PnL 차이 문서화
- [ ] 동일 시점 shadow vs replay 결과 차이 원인이 해석 가능 (데이터 지연, scanner 주기 등)

---

## Phase 5 — Tiny Live (실주문, 고정 미니 사이즈)

목표: **진짜 주문을 넣는다. 다만 사이즈는 무시해도 되는 수준.**

### 5.1 사이즈 고정
- `execution_mode=live` 이면서 `live_max_notional_per_order=$20` 같은 **하드 캡**.
- 자동화 사이징 함수(`_position_size`) 는 동일 경로를 타되, 최종 단계에서 `min(calc, live_cap)` 로 클립.

### 5.2 주문 유형 제한
- 시장가 금지. `LIMIT` + `time_in_force=DAY` 만 허용.
- entry 주문: `mid + X bps` 이내 공격적 limit, 미체결 시 Y초 내 자동 취소.
- exit 주문: bid 바로 밑 또는 최우선 bid hit. stop-out 은 정지가격 아닌 **stop-limit** (슬리피지 한도 포함).

### 5.3 운영 제어판(최소)
- CLI: `psradar execution status / killswitch on / killswitch off / flatten-all`.
- `flatten-all` 은 현재 열린 포지션만 청산, 신규 진입 안 생김.
- 전 기능 모두 감사 로그 필수.

### 5.4 알람
- Slack / Telegram / 메일 중 하나로:
  - reject rate > 임계치
  - reconciliation mismatch
  - daily loss lock 발동
  - killswitch 상태 변화

### Exit Criteria
- [ ] 최소 20 거래일 Tiny Live 가동, 주문 reject rate < X%
- [ ] 이상 이벤트(halt, stale spike, 브로커 timeout) 각 최소 1회 경험 후 시스템이 안전하게 복귀
- [ ] PnL 이 shadow 대비 유의미하게 나쁘지 않음 (슬리피지 모델 검증)

---

## Phase 6 — Scale-up 게이트

목표: 사이즈를 올리는 기준을 **주관이 아닌 규칙**으로 만든다.

### 6.1 스케일업 공식
- 단계: `Tier 0: $20` → `Tier 1: $100` → `Tier 2: $500` → ...
- 승격 조건 (모두 AND):
  - 최근 N 거래일 실현 PnL > 0
  - max drawdown < Y%
  - reject rate < Z%
  - reconciliation mismatch 0건
- 강등 조건: 위 중 하나라도 깨지면 즉시 한 단계 다운.

### 6.2 전략별 분리 한도
- `intraday` / `multiday` 각각 별도 티어 관리. 한 엔진이 깨져도 다른 엔진은 안 흔들리게.

### 6.3 일일 자본 배분
- 전체 capital 중 `live_fraction` 만 엔진에 할당. 나머지는 현금.
- 승격마다 `live_fraction` 을 단계적으로 올린다.

### Exit Criteria
- [ ] 자동 승격/강등 로직이 실제로 돌고 이력이 남는다
- [ ] 3개월 연속 수익 + 최대 DD 규정 내 → 정식 운용으로 전환 판단 가능

---

## Phase 7 — 장기 운영 Hardening (동시 진행 가능)

이 항목들은 위 Phase 와 **병렬로** 진행해도 된다. Phase 5 이전엔 꼭 끝내야 하는 것만 ★ 로 표시.

- ★ **NTP/시계 동기**: 서버/런처 시작 전 clock offset 점검.
- ★ **토큰 수명 관리**: KIS access token 만료 before-expire refresh, 실패 시 killswitch.
- ★ **프로세스 재시작 안전성**: 진행 중 `WORKING` 주문이 있는 상태에서 강제 종료 후 재시작 → 다음 부팅시 reconciliation 이 먼저 돈다.
- **백테스트/재현 파이프라인 정비**: replay 가 실데이터 기록을 그대로 다시 돌릴 수 있게.
- **비밀값 관리**: `.env` 를 벗어나 OS keychain / 1Password CLI / macOS `security` 도입.
- **컴플라이언스 체크**: KIS 해외주식 주문 사전 약관, 세금/거래조건, 프리/정규/애프터 세션 규칙, 배당/액면분할 이벤트.
- **벤치마크 다양화**: 현재의 `baseline_pct / baseline_volume` 외에 "장중 SPY hold", "무거래 cash hold" 도 리포트에 포함.
- **문서**: `docs/OPERATIONS_KO.md` 에 Tiny/Shadow/Live 모드별 런북, `docs/STATUS.md` 는 Phase 진행 상태만 요약.

---

## 우선순위 추천 경로

바로 시작할 Phase 순서는 아래가 가장 안전하다.

1. Phase 1.2, 1.3 (데이터 정규화/halt 게이트) — 현재 `docs/STATUS.md` 에 명시된 한계를 먼저 지운다.
2. Phase 2.1, 2.2 (BrokerAdapter 뼈대 + dry_run KIS) — 구조만 먼저 바꾼다, 동작 변화 없음.
3. Phase 3.1, 3.2 (RiskGate + OrderLifecycle) — 실주문 전에 2중 방어선.
4. Phase 4 전체 (Shadow) — 여기서 최소 2주 운영.
5. Phase 5 Tiny Live — 반드시 최소 사이즈로.
6. Phase 6 Scale-up.

각 단계는 **이전 단계의 Exit Criteria 가 모두 체크됐을 때만** 다음으로 넘어간다.

---

## 이 문서의 운영 규칙

- Phase 가 끝날 때마다 해당 섹션의 Exit Criteria 를 체크하고, `docs/STATUS.md` 의 "다음 우선순위" 를 이 문서에서 도출한다.
- 구현이 이 문서와 달라지면 **구현이 아니라 이 문서를 먼저 고친다**.
- 실매매 전환 직전 주 1회, 실매매 시작 후 매일 1회 리뷰 시점을 둔다.
