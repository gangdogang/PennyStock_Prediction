# 리팩터 후속 정리 계획

이 문서는 `BACKTEST_ROADMAP_KO.md` Step 1-4 구현 리팩터 직후의 회귀·구조 문제를 정리하기 위한 단기 계획이다.
모두 완료되기 전에는 백테스트 실행 / KPI 리포트 구현으로 넘어가지 않는다.

**에이전트 지침**: 이 문서의 Step 은 순서대로 진행한다. Step A 가 끝나기 전 Step B/C 를 병렬로 건드리지 않는다. 각 Step 완료 시 `docs/STATUS.md` 를 갱신한다.

---

## 배경

`paper_trading.py` 의 체결 모델을 리팩터하면서 `_buy_fill_price` / `_sell_fill_price` 의 시그니처와 반환값이 바뀌었다.
intraday 경로는 갱신됐지만 `multiday_engine.py` 의 두 호출부가 옛 시그니처 그대로여서 5개 테스트가 실패 중이다.
동시에 `paper_trading.py` 가 2163줄, `multiday_engine.py` 가 995줄로 god-class 화가 진행됐다.

이 문서의 목표는 **회귀 해소 + 재발 방지 구조 + 버킷 불변식 + 튜닝 가능성** 을 이번 브랜치 머지 전에 맞추는 것이다.

---

## Step A — 회귀 해소 (즉시)

**목표**: `multiday_engine` 을 새 체결 모델 시그니처에 맞추고 기존 테스트를 녹색으로 되돌린다.

### 해야 할 것

- `src/penny_stock_radar/services/multiday_engine.py:418` (winner add 경로)
  - `self._buy_fill_price(row)` → `self._buy_fill_price(row, market_phase=market_phase, requested_quantity=requested_quantity)` 로 교체
  - 반환 7-tuple `(quantity, remaining_quantity, fill_status, fill_price, fill_reference_price, fill_slippage_pct, transaction_cost)` 를 모두 받는다
  - `transaction_cost` 를 `run.cash_balance`, `position.cost_basis`, `position.fees_paid_total`, `run.total_transaction_cost` 에 반영
  - `remaining_quantity > 0` 인 경우 `partial_fill_cap` 이유 추가
  - `fill_status` 를 `PaperOrder` 에 기록
- `src/penny_stock_radar/services/multiday_engine.py:684` (starter entry 경로)
  - 동일하게 새 시그니처로 교체
  - `average_entry_price` 계산식을 intraday 와 동일하게 `(notional + transaction_cost) / quantity` 로 맞춘다
  - `PaperPosition` / `PaperOrder` 필드도 새 키(`fees_paid_total`, `requested_quantity`, `remaining_quantity`, `fill_status`, `transaction_cost`)를 모두 채운다
- 참조 구현: `src/penny_stock_radar/services/paper_trading.py:529-616` (intraday winner add 경로)
- 기존 intraday 경로와 동일한 부기 순서를 유지할 것 (cash → cost_basis → fees → position → order)

### Exit Criteria

- [ ] `pytest tests/test_multiday_engine.py` 가 모두 통과
- [ ] `pytest tests/` 전체 통과
- [ ] multiday 스타터 / winner add 에서도 `transaction_cost` 가 0 이 아님을 테스트로 확인
- [ ] multiday 에서 `remaining_quantity > 0` 인 partial fill 케이스 테스트 1건 추가

---

## Step B — Bucket 불변식 하드 가드

**목표**: `momentum_only` 버킷에 예측기 편향이 새는 경로를 구조적으로 차단한다.
60/40 분할을 독립 포트폴리오로 바꾼 이유가 이 불변식이었는데, 현재는 설정(k1, k2)에만 의존한다.

### 해야 할 것

- `src/penny_stock_radar/services/paper_trading.py:_predictor_weight` (약 L1947) 에 버킷 가드 추가
  ```python
  if self.bucket == MOMENTUM_ONLY_BUCKET:
      return 0.0
  ```
- 동일 원칙으로 `_refresh_predictor_scores` 도 `momentum_only` 에서는 dict 를 비운 상태로 유지
- `predictor_score:` / `predictor_weight:` reason 은 `momentum_only` 에서 남지 않아야 한다
- 테스트:
  - `momentum_only` 버킷에서 k1=k2=1.0 으로 강하게 세팅해도 동일 시나리오 거래 수 / PnL 이 k1=k2=0 일 때와 동일한지 확인
  - `predictor_weighted` 버킷에서만 `predictor_score` reason 이 기록되는지 확인

### Exit Criteria

- [ ] 위 두 테스트 추가 및 통과
- [ ] 전체 테스트 통과 유지
- [ ] `docs/BACKTEST_ROADMAP_KO.md` Step 3 에 "버킷 독립은 구조적 불변식" 한 줄 명시

---

## Step C — FillModel 분리

**목표**: 체결 모델을 `PaperTradingEngine` 에서 꺼내 독립 클래스로 만든다. 이번 같은 silent regression 을 구조적으로 불가능하게 만드는 것이 핵심이다.

### 해야 할 것

- 신규 파일 `src/penny_stock_radar/services/fill_model.py` 에 `FillModel` 클래스 생성
  - 이동 대상 메서드: `_buy_fill_price`, `_sell_fill_price`, `_capped_fill_quantity`, `_transaction_cost`, `_spread_abs`, `_spread_penalty_abs`, `_session_spread_multiplier`, `_is_halt_resume_row`
  - 의존성: `AppSettings` 만 주입받는다 (다른 엔진 상태는 받지 않는다)
  - 반환 타입은 `@dataclass(frozen=True, slots=True) FillResult` 로 명시적 타입 지정 (7-tuple unpack 실수 방지)
- `PaperTradingEngine.__init__` 에 `self.fill_model = FillModel(settings)` 주입
- `IntradayTradingEngine` / `MultidayTradingEngine` 둘 다 같은 인스턴스를 참조
- `PaperTradingEngine` 내부 호출은 `self.fill_model.buy(...)` 로 교체
- 기존 `_buy_fill_price` / `_sell_fill_price` 은 제거 (backward shim 금지)

### Exit Criteria

- [ ] `paper_trading.py` 행 수가 500줄 이상 감소
- [ ] `multiday_engine.py` 의 체결 로직이 `self.fill_model.*` 경로로만 접근
- [ ] 리팩터 전/후 동일 입력 시나리오에서 동일 체결 결과 (golden-file 회귀 테스트 1건)
- [ ] 전체 테스트 통과

---

## Step D — Predictor 가중치 settings 승격

**목표**: `PremktPredictor._score_entry` 의 스칼라 가중치를 `AppSettings` 로 올려 Step 6 (out-of-sample / walk-forward) 에서 코드 수정 없이 튜닝 가능하게 만든다.

### 해야 할 것

- `src/penny_stock_radar/config.py` 에 가중치 필드 추가
  ```
  predictor_weight_total_score: float = 11.0
  predictor_weight_catalyst: float = 8.0
  predictor_weight_technical: float = 4.0
  predictor_weight_sympathy: float = 3.0
  predictor_weight_market_context: float = 4.0
  predictor_weight_social: float = 2.0
  predictor_weight_low_float_bonus: float = 3.0
  predictor_weight_filing_bonus: float = 6.0
  predictor_weight_multi_theme_bonus: float = 4.0
  ```
- 모두 `_nonnegative_floats` 밸리데이터에 포함
- `PremktPredictor._score_entry` 의 상수 전부를 `self.settings.predictor_weight_*` 로 교체
- `_max_hold_days` 의 임계값 (`catalyst_score >= 1.0`, `total_score >= 6.5`, `total_score >= 4.5`) 도 settings 로 올린다
  ```
  predictor_max_hold_days_tier3_total: float = 6.5
  predictor_max_hold_days_tier3_catalyst: float = 1.0
  predictor_max_hold_days_tier2_total: float = 4.5
  ```

### Exit Criteria

- [ ] 기본값으로 돌린 결과가 기존과 bit-exact 일치
- [ ] `.env` 로 가중치를 바꾸면 predictor 점수가 바뀌는 테스트 1건
- [ ] `.env.example` 에 새 항목 주석 포함

---

## Step E — 회귀 방지 골든 테스트

**목표**: 이후 체결 모델 / 가중치 튜닝에서 의도치 않은 회귀를 즉시 잡는다.

### 해야 할 것

- `tests/test_regression_golden.py` 신규 추가
- 고정 입력(MarketActivity 시퀀스 + PremktPrediction 고정값)으로 엔진을 돌려 나온 `PaperOrder` 리스트를 JSON 스냅샷으로 비교
- 케이스 3개
  - intraday predictor_weighted, k1=k2=0
  - intraday momentum_only
  - multiday predictor_weighted (starter → winner add → day2 exit 흐름 포함)
- 스냅샷은 `tests/golden/` 하위에 고정, 변경 시 PR 에서 diff 가 반드시 보이게 한다

### Exit Criteria

- [ ] 위 3개 케이스가 golden 비교로 통과
- [ ] k1 / k2 를 0 에서 변경하면 스냅샷이 달라지는 것을 별도 테스트로 보장
- [ ] 체결 모델 / 가중치 변경 PR 에서 의도된 diff 만 생기는지 reviewer 가 한눈에 볼 수 있는 구조

---

## Step F — 페니 리얼리즘 보강

**목표**: Step 4 현실화가 실제 페니 특성에 더 맞도록 두 축 보강. Step A-E 완료 후 진행.

### 해야 할 것

- **시총 축 볼륨 cap 분기 추가**
  - 현재 cap 은 일거래대금만 기준으로 10% / 5% / 2%
  - 시총 티어도 병행:
    - `market_cap >= 500M` → 기존 거래대금 티어대로
    - `market_cap < 500M` → cap 을 한 단 강하게 (각 티어의 절반)
  - `_capped_fill_quantity` 가 두 축 중 **더 빡빡한 쪽** 을 채택
- **할트 재개 시간 감쇠**
  - 현재 `_is_halt_resume_row` 는 boolean
  - 재개 후 경과 시간 `t` 를 입력받아 가산 스프레드를 `spread * 2 * exp(-t / τ)` 형태로 감쇠 (τ 기본 3분)
  - 첫 봉이 가장 깊고 3-5분 뒤에는 일반 스프레드 수준으로 수렴
- 새 settings 항목은 `AppSettings` 에 추가 (`paper_halt_resume_decay_minutes` 등)

### Exit Criteria

- [ ] 시총 작은 종목이 큰 종목보다 더 빡빡한 cap 이 적용되는 테스트 1건
- [ ] 할트 재개 직후 vs 5분 뒤 슬리피지 차이 테스트 1건
- [ ] 전체 테스트 통과

---

## Step G — 문서 / STATUS 정합성

**목표**: `STATUS.md` 와 백테스트 로드맵이 현재 구현 상태를 정확히 반영.

### 해야 할 것

- `docs/STATUS.md` 갱신
  - "다음 우선순위" 에 `BACKTEST_ROADMAP_KO.md` 의 현재 Step 을 기록
  - multiday KPI / report 통합은 로드맵 Step 5 로 이동했음을 명시
- `docs/BACKTEST_ROADMAP_KO.md`
  - Step 3 에 "버킷 독립은 구조적 불변식" 문장 추가 (Step B 와 연동)
  - Step 2 에 "k1=k2=0 golden 스냅샷이 pre-refactor 와 bit-exact 일치해야 한다" 문장 추가
- `docs/multiday_plan/PLAN_TRACKER_KO.md`
  - Step A 로 해소된 회귀를 "지난 이슈" 로 한 줄 남기고 tracker 자체는 archive 검토
- `AGENTS.md` 의 문서 순서에 `BACKTEST_ROADMAP_KO.md` 와 이 파일을 명시적으로 포함

### Exit Criteria

- [ ] 위 4개 문서가 서로 모순되지 않고 현재 Step 을 가리킴
- [ ] 새 컨텍스트에서 `AGENTS.md → README → STATUS → BACKTEST_ROADMAP → REFACTOR_FOLLOWUP` 순으로 읽으면 현재 상태 파악 가능

---

## 전체 순서 요약

```
Step A  (회귀 해소, 다른 모든 작업의 전제)
  ↓
Step B  (버킷 불변식, A 완료 후)
  ↓
Step C  (FillModel 분리, A·B 완료 후)
  ↓
Step D → Step E   (predictor settings + 골든 테스트, C 이후 병렬 허용)
  ↓
Step F  (페니 리얼리즘, E 완료 후)
  ↓
Step G  (문서 정합성, 언제든)
  ↓
→ `BACKTEST_ROADMAP_KO.md` Step 5 (KPI / 리포트) 로 복귀
```

---

## 문서 운영 규칙

- 각 Step 완료 시 해당 Exit Criteria 를 체크하고 `docs/STATUS.md` 를 갱신한다.
- Step A 가 통과하기 전에 백테스트 실행 / KPI 작업을 시작하지 않는다.
- 구현이 이 문서와 달라지면 구현보다 이 문서를 먼저 고친다.
- Step A-G 가 모두 완료되면 이 문서는 `archive/` 로 이동시킨다.
