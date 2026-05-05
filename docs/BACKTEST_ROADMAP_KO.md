# 백테스팅 로드맵

이 문서는 유의미한 백테스팅 실행 전까지 완료해야 할 작업을 단계별로 정리한 것이다.

**에이전트 지침**: 이 문서를 기반으로 작업할 때 가용할 수 있는 최대한 많은 에이전트를 가용해라.

LIVE_TRADING 계획(실매매 전환)은 이 로드맵이 완료되고 백테스팅 결과가 검증된 이후에 시작한다.

## 전제 — 1개월은 "검증"이 아니다

- 페니스탁 모멘텀 전략에서 1개월 백테스트는 버킷당 ~20-60 거래 수준이다.
- 통계적으로 predictor의 edge를 의미있게 판단하기에 부족하고, 단일 장세(regime)에만 노출된다.
- 따라서 1개월은 **sanity check** 로만 취급하고, 실매매 전환 판단에는 **과거 데이터 재생 기준 최소 3개월**, 가능하면 **walk-forward / rolling** 평가를 사용한다.
- 파라미터 튜닝 구간과 검증 구간은 반드시 분리(out-of-sample)한다.
- 이 3개월 기준은 실제 시간을 기다리자는 뜻이 아니다. 과거 데이터를 모아 재생 백테스트로 빠르게 돌리는 검증 단위다.

## Structural Edge / Bias / Benchmark Gate

전략 개선은 "좋은 feature" 를 찾기 전에 이 시장에서 돈을 벌 구조적 이유가 있는지 먼저 반증한다. 페니스탁 intraday 모멘텀은 정보 우위, 실행 우위, 보유 우위가 없으면 기본 prior 를 음수로 둔다.

### 먼저 의심할 것

- **Universe adverse selection**: watchlist 가 이미 retail breakout 꼭대기 종목을 모으는 구조인지 확인한다. 같은 universe 에서 random entry / random time benchmark 를 같이 돌리지 않으면 entry engine 문제인지 universe 문제인지 구분하지 않는다.
- **Survivorship bias**: delisting, 합병, reverse split, ticker change 종목이 historical universe 에 포함되는지 확인한다. 살아남은 티커만으로 만든 DB 는 stop rate 와 tail risk 를 과소평가할 수 있다.
- **Cost realism**: L1 bid/ask, spread, fee, participation penalty 를 반영한 cost-adjusted expectancy 를 먼저 본다. spread 가 큰 페니스탁에서는 1R 도달률 몇 퍼센트 개선이 round-trip cost 에 먹힐 수 있다.
- **Stop geometry**: fixed 5% stop 기준의 `reached_1r` / `stop_before_1r` 는 setup quality 가 아니라 ATR/volatility/spread 함수일 수 있다. ATR-normalized R, structure-stop R, spread-adjusted R 을 병기하기 전에는 edge label 로 쓰지 않는다.
- **Trade horizon**: intraday 당일 청산만 보지 않는다. 같은 universe/signal 에 대해 D+1~D+5 catalyst-aware hold ablation 을 병행해 edge 가 horizon 문제인지 확인한다.

### 필수 null / diagnostic benchmark

- same-universe random entry
- same-universe random time
- naive top-gainer / volume-leader baseline
- cash/no-trade baseline
- opposite-side diagnostic

`opposite-side diagnostic` 은 페니스탁 short 의 borrow/locate/SSR 제약 때문에 실거래 가능 전략으로 해석하지 않는다. 같은 신호가 retail crowding/fade 신호인지 확인하는 진단으로만 쓴다.

### Reject 기준

- 월별 일관성 없이 특정 달, 특정 심볼, 특정 날짜 제거 후 무너지는 feature 는 폐기한다.
- cost-adjusted expectancy 가 음수이면 gross PnL 또는 1R 도달률이 좋아도 strategy 후보로 승격하지 않는다.
- null benchmark 대비 초과 성과가 없으면 entry label 또는 setup_state 개선으로 해석하지 않는다.
- universe/cost/horizon 문제가 확인되면 intraday filter tuning 을 멈추고 universe construction, execution model, holding horizon 전환을 먼저 검토한다.

### Overnight falsification gate

이 gate 는 feature tuning 전 필수 단계다. 목적은 전략을 개선하는 것이 아니라 전략 가정이 먼저 깨지는지 확인하는 것이다.

Preflight:

- `data/backtest_lab/` DB 사본과 `run_manifest.json` 을 남길 수 있는 쓰기 경로가 있어야 한다.
- point-in-time universe, SEC cutoff, L1/minute coverage, survivorship inventory 상태를 먼저 기록한다.
- 기존 ablation 결과를 pass 근거로 재사용하지 않는다.

Command:

```bash
./scripts/psradar run-falsification-audit --run-id overnight_$(date +%Y%m%d)
```

point-in-time universe blocker 를 먼저 분리할 때:

```bash
./scripts/psradar audit-pit-universe-reconstruction --run-id pit_audit_$(date +%Y%m%d)
```

strategy entry timing 을 보존한 random-entry null 을 포함할 때:

```bash
./scripts/psradar run-falsification-audit --run-id matched_$(date +%Y%m%d) --strategy-run-dir data/backtest_lab/replays/<run_id> --strategy-bucket predictor_weighted
```

`same_universe_random_entry` 는 trade log 의 entry timing 만 가져온다. random replacement universe 는 exact PIT same-date universe 여야 하며, 같은 분봉 bar overlap 과 cost sample 이 없으면 blocked 처리한다.

필수 산출물:

- governance/budget
- data inventory
- point-in-time / survivorship blocker
- L1/minute spread cost audit
- same-universe random-time null benchmark
- fixed / ATR / structure stop geometry
- benchmark suite status
- final `PASS / FAIL / BLOCKED` gate summary

판정:

- `PASS`: Phase 0 blocker 가 없고, 필수 benchmark suite 가 준비돼 Phase 1 stop-out 분석으로 넘어갈 수 있는 상태다. edge 승인이나 live 승인으로 해석하지 않는다.
- `FAIL`: matched strategy-vs-null 비교에서 cost-adjusted expectancy 가 음수이거나 null 대비 초과 성과가 없거나 집중도 제거 후 무너진다.
- `BLOCKED`: coverage, survivorship, point-in-time, L1/spread cost, benchmark suite 중 하나라도 부족해 판단 불가다.

`PASS` 전에는 entry/setup/score/filter/stop/sizing tuning 을 금지한다. `FAIL` 은 hypothesis 폐기, `BLOCKED` 는 데이터/benchmark 보강만 허용한다.

PIT universe 복구 원칙:

- exact `snapshot_role=point_in_time` + same `market_date` universe 만 edge 판단용 PIT 로 본다.
- historical minute bars 에서 만든 bar-derived universe 는 diagnostic-only 다. 이는 null plumbing smoke 에는 쓸 수 있지만, intraday 전체 바를 본 결과라 lookahead/adverse-selection 위험이 있으므로 blocker 를 해소하지 않는다.
- stale prior PIT snapshot 을 날짜 D에 자동 재사용하지 않는다. 재사용하려면 staleness 정책과 missing/new symbol impact 를 별도 리포트로 먼저 검증한다.
- 이미 존재하는 scan 을 PIT 로 승격할 때는 `tag-pit-universe-scan --scan-id ... --market-date ...` 를 쓰고, scan `created_at` 이 D 08:00 ET cutoff 이후이면 기본적으로 거부한다. `--allow-after-cutoff` 는 diagnostic plumbing 용도일 때만 사용한다.

## 실행 원칙 — 기다리지 않는 백테스트 루프

성능 개선은 "실시간으로 3개월 기다리기"가 아니라 아래 루프로 진행한다.

1. **2일 이하 smoke replay**
   - 코드/데이터 연결 버그를 빠르게 잡는다.
   - predictor score/weight 기록, 버킷 분리, 주문/포지션/거래 로그 일관성, CSV export schema 를 검증한다.
   - 이 단계 결과는 성능 우위 판단에 쓰지 않는다.
2. **5-10 거래일 sanity replay**
   - 손절, 슬리피지, 거래비용, daily lock, halt/stale guard 가 정상적으로 작동하는지 확인한다.
   - 한 번의 이상 거래가 전체 결과를 지배하는지 확인한다.
3. **1개월 calibration replay**
   - 파라미터 후보를 좁히는 용도다.
   - 여전히 최종 검증이 아니며, tuning set 으로만 취급한다.
   - calibration 에서 발견한 score cutoff 는 frozen hypothesis 로만 다음 검증에 넘기고, calibration 손익 개선만으로 live 전략으로 해석하지 않는다.
4. **3개월 이상 out-of-sample replay**
   - 튜닝이 끝난 설정을 고정한 뒤 단 1회 실행한다.
   - 실매매 전환 판단에는 이 결과와 shadow 결과를 함께 사용한다.

각 루프는 반드시 `run_manifest.json`, `paper_backtest_kpis.csv`, `paper_trade_log.csv`, `paper_bucket_trade_diff.csv`, `paper_bucket_pair_diff.csv`, `paper_predictor_kpis.csv`, `paper_execution_quality.csv` 를 남겨야 한다. setup_state 검증 run 은 추가로 `paper_setup_features.csv`, `paper_setup_state_kpis.csv`, `paper_setup_transition_matrix.csv`, `paper_add_trim_runner_diagnostics.csv` 를 남긴다.

## Step -1 — 성능평가 배선 검증

**목표**: 전략 성능을 논하기 전에 결과 파일이 실제로 predictor/bucket 차이를 측정하고 있는지 검증한다. 이 단계가 실패하면 Step 0 이후의 장기 백테스트도 해석할 수 없다.

### 현재 관찰된 문제

- 최근 paper 결과에서 `predictor_weighted` 와 `momentum_only` 의 거래/손익이 완전히 동일했다.
- `paper_trade_log.csv` 의 `predictor_score`, `predictor_weight` 가 모두 비어 있었다.
- `paper_predictor_kpis.csv` 에서 `candidate_count=0` 인데 `triggered_candidate_count` 와 predicted trade 가 존재했다.
- 따라서 현재 CSV만으로는 predictor edge 를 판단할 수 없다.

### 해야 할 것

- **Predictor lineage 고정**
  - 주문 생성 시점의 `predictor_score`, `predictor_weight`, `prediction_generated_at`, `prediction_cutoff_at`, `prediction_source` 를 order/position/trade log 에 끝까지 전파한다.
  - 후보가 아닌 종목은 `predictor_weight=0.0` 과 `predicted=false` 로 명시 기록한다.
- **KPI 분모 정의 고정**
  - `candidate_count`: cutoff 이전 predictor 후보 수.
  - `triggered_candidate_count`: 후보 중 실제 진입 조건을 만족해 주문까지 간 수.
  - `predicted_trade_count`: predictor 후보로 태그된 전체 거래 수.
  - `predictor_hit_rate_pct`: predictor 후보 거래 중 net PnL 또는 R 기준 수익 거래 비율.
- **Bucket divergence smoke test**
  - synthetic predictor 점수 fixture 로 `predictor_weighted` 는 진입하고 `momentum_only` 는 진입하지 않는 케이스를 만든다.
  - `paper_bucket_trade_diff.csv` 에 최소 1건의 non-zero `pnl_diff` 가 생기는 회귀 테스트를 추가한다.
- **Performance review gate**
  - 최신 CSV를 읽어 아래 조건을 검사하는 CLI 또는 테스트를 추가한다.
    - 종료 거래가 있는데 `predictor_score`/`predictor_weight` 가 전부 비어 있으면 fail.
    - 두 bucket 의 closed trade 가 모두 동일하고 `pnl_diff` 가 전부 0이면 warning 또는 fail.
    - `candidate_count=0` 이면서 `triggered_candidate_count>0` 이면 fail.
    - `run_manifest.json` 이 없으면 warning.
- **Run manifest**
  - 각 replay/export 마다 기간, 데이터 소스, DB path, settings hash, git sha, 실행 명령, timezone, seed 를 `run_manifest.json` 으로 남긴다.

### Exit Criteria

- [x] `paper_trade_log.csv` 의 predictor 관련 컬럼이 closed trade 에서 비어 있지 않음
- [x] `candidate_count`, `triggered_candidate_count`, `predicted_trade_count` 의 분모/분자 정의가 테스트로 고정됨
- [x] synthetic smoke 에서 `predictor_weighted` 와 `momentum_only` 의 거래 diff 가 발생함
- [x] 최신 결과 CSV에 대한 performance review gate 가 pass 또는 명시적 warning 을 출력함
- [x] 모든 성능평가 산출물에 `run_manifest.json` 이 동반됨

### 완료 기록

- 2026-04-21: `paper_trade_log.csv` 에 predictor lineage fallback 과 `prediction_generated_at` / `prediction_cutoff_at` / `prediction_source` 컬럼을 추가했다.
- 2026-04-21: `export_predictor_kpis()` 의 CSV boolean 해석을 고쳐 `"False"` 문자열이 truthy 로 취급되지 않게 했다.
- 2026-04-21: `run_manifest.json`, `paper_performance_gate.json`, `psradar review-paper-performance` 를 추가했다.
- 2026-04-21: 관련 smoke 와 전체 품질 게이트를 확인했다. `./scripts/check_quality.sh` 결과: `199 passed`; coverage gate 상태 파일은 아직 미생성이라 요약만 출력됨.
- 2026-04-21: 기존 `momentum_only` 가 pure momentum 이 아니라 watchlist-aware momentum 이었음을 기준 문서에 반영하고, Step 3 을 3-bucket within-scan ablation 으로 보강하기로 했다.
- 2026-04-23: Windows paper drive 런처가 평가 run 에서 predictor effect 를 기본 활성화(`k1=1.0`, `k2=1.0`)하도록 고정했고, `run_manifest.json`/`launcher_manifest.json` 에 predictor effect 와 bucket policy 를 남긴다.
- 2026-04-23: `review-paper-performance` 는 predictor effect disabled, k1/k2 0, enabled-but-identical closed trade 결과를 구분해 fail/warning 을 낸다. predictor effect disabled run 과 Step 0 coverage 60% 미만 run 은 predictor edge 판단 근거로 쓰지 않는다.

---

## Step 0 — 백테스트 데이터 인프라

**목표**: 백테스트 결과가 "garbage-in"이 되지 않도록 데이터 기반을 먼저 고정한다. 이 단계가 미완이면 Step 4 현실화는 의미를 잃는다.

### 해야 할 것

- **Point-in-time universe snapshot**
  - 오늘의 티커 universe 로 과거를 돌리면 퇴출/역분할/합병 종목이 빠져 survivorship bias 가 생긴다.
  - 각 백테스트 날짜 D 에 대해 "D일 기준으로 상장돼 있던 티커" 스냅샷을 저장하고 재현할 수 있어야 한다.
  - KIS master / listings provider 에서 과거 스냅샷을 tag 하고 DB 에 저장하는 경로를 만든다.
- **SEC 공시 look-ahead 차단**
  - `filing_summary` / watchlist 입력은 백테스트 날짜 D 프리장 cutoff (기본 08:00 ET) 이전에 `filed_at` 이 있는 공시만 사용해야 한다.
  - 현재 `watchlist_builder` 가 latest 기준으로 끌어오는 경로가 있다면 백테스트 모드에서는 `filed_at <= D 08:00 ET` 필터를 강제한다.
- **Historical L1 quote / minute bar**
  - Step 4 의 bid/ask 체결 모델을 돌리려면 과거 L1 quote 또는 최소한 minute OHLC + spread 가 필요하다.
  - 이 저장소의 기준 데이터 소스는 `KIS` 로 고정한다. Step 0 구현은 KIS historical/minute 경로를 우선하고, 다른 provider 확장은 우선순위 밖으로 둔다.
  - 현재 저장소에는 `psradar backfill-kis-minute`, `psradar capture-kis-l1`, `psradar capture-kis-l1-window`, `psradar report-backtest-coverage` 경로가 추가되었다. `capture-kis-l1-window` 는 반복 capture 후 latest coverage report/gate 를 갱신한다. 다음 단계는 이 경로로 실제 coverage 를 채워 60% 기준을 검증하는 것이다.
  - L1 `snapshot_date` mismatch 또는 120분 초과 timestamp drift 는 coverage report note 와 gate failure 로 반영한다.
  - 커버리지 60% 미만이면 전략 백테스트 이전에 소스 보강이 우선이다.
- **Halt / LULD 이벤트 기록**
  - 페니 종목은 halt 가 잦다. 과거 halt 이벤트(시각, 사유, 재개가)를 수집한다.
  - 소스가 없으면 최소한 minute bar gap + 거래량 0 구간으로 halt 를 추정하는 fallback 을 둔다.

### Exit Criteria

- [ ] 임의의 과거 날짜 D 에 대해 "D일 기준 universe / 공시 / L1 quote"가 재현 가능
- [ ] 현재 universe 만으로 돌린 결과와 point-in-time universe 결과의 차이 리포트 1건 확보
- [ ] L1 quote 커버리지 리포트 (대상 티커 % / 시간대 %)

---

## Step 1 — 예측기 분리 (신규 서비스 추가)

**목표**: 프리마켓 스코어링을 주문 로직과 독립된 재사용 서비스로 분리한다. **기존 `MultidayTradingEngine` 을 삭제하거나 이름을 바꾸지 않는다.** 이미 구현된 starter/add/replacement/exit 규칙은 보존한다.

### 해야 할 것

- 신규 모듈 `services/premkt_predictor.py` 에 `PremktPredictor` 클래스를 추가한다.
- 기존 엔진의 주문 로직에 손대지 않고, 스코어링에만 쓰이던 입력/피처 계산을 추출해 이 서비스가 공유한다.
- 출력 형태:
  ```
  {
    symbol: str,
    score: float,                 # 0-100 연속값
    max_hold_days: int,           # 추천 최대 보유일
    entry_rationale: str,         # 진입 근거 요약
    themes: list[str],            # biotech / ai / energy 등
    filing_summary: str,          # SEC 공시 요약 (filed_at <= cutoff)
    generated_at: datetime,       # 스냅샷 시각
  }
  ```
- 예측기는 장 시작 전 1회 실행하고, 결과를 DB 테이블(`premkt_predictions`) 또는 일자별 파일로 저장한다.
- `MultidayTradingEngine` 은 그대로 둔 채 `PremktPredictor` 출력을 참고 입력으로 받을 수 있게 한다 (옵셔널).

### Exit Criteria

- [ ] `PremktPredictor.run()` 이 주문 테이블에 어떤 기록도 남기지 않음
- [ ] 후보 리스트 + 점수 + 추천 임계일 출력이 DB/파일에 저장됨
- [ ] 기존 `MultidayTradingEngine` / `IntradayTradingEngine` 동작 회귀 없음 (기존 paper trading 결과 동일)
- [ ] 예측기 입력에서 `filed_at > cutoff` 공시가 제외됨을 테스트로 확인

---

## Step 2 — 예측기 연결 (연속 가중치)

**목표**: 예측기 결과를 `IntradayTradingEngine` 이 연속 가중치로 소비해 진입 threshold / 포지션 사이즈를 조절한다. 기존의 "후보/비후보 × 강/약 모멘텀" 2×2 매트릭스는 정보 손실이 크므로 연속 가중치로 대체한다.

### 가중치 정의

- `predictor_weight ∈ [0, 1]`: 예측기 점수에서 파생된 값
  - 점수 ≥ 80 → 1.0
  - 점수 ≤ 40 → 0.0
  - 그 사이는 선형 보간
- 비후보 종목은 `predictor_weight = 0.0` 으로 취급 (진입이 차단되지는 않음)

### 규칙

- **entry threshold 감산**: 모멘텀 점수 threshold 를 `base_threshold - k1 * predictor_weight` 로 낮춤
- **position size 계수**: `size = base_size * (1 + k2 * predictor_weight)` (단, `max_size` 캡 유지)
- **k1, k2 는 settings 로 승격**하여 튜닝 가능하게 한다
- `k1 = k2 = 0` golden 스냅샷은 pre-refactor 기준과 bit-exact 일치해야 한다
- 테스트 케이스는 직관 확인용으로 4개 코너 케이스(강/약 × 후보/비후보)를 유지하되, 로직은 연속값

### Exit Criteria

- [ ] 예측기 점수가 높은 종목이 비후보 대비 낮은 모멘텀에서도 진입하는 케이스 확인
- [ ] 비후보 종목이 기존 기준대로 엄격하게 진입되는 것 확인 (regression 없음)
- [ ] `k1 = k2 = 0` 설정 시 기존 intraday 엔진과 동일 동작 (feature flag off)
- [ ] 단위 테스트: 4가지 코너 케이스 + 중간값 2개

---

## Step 3 — 버킷 분리 (독립 포트폴리오)

**목표**: 가상 자금을 세 **독립 포트폴리오** 로 나눠 같은 scanned activity universe 안에서 predictor score/weight, watchlist metadata, live momentum 의 기여를 분해한다. 통계적 해석을 명확히 하기 위해 60/40 분할 대신 **병렬 100% 포트폴리오** 를 돌린다 (동일 초기 자본, 동일 기간).

### v1 구조

| Bucket | Universe | Predictor score/weight | Watchlist metadata | Live momentum | 의미 |
| --- | --- | --- | --- | --- | --- |
| `predictor_weighted` | watchlist/live scan activity universe | 사용 | 사용 | 사용 | 현재 조합 전략 |
| `momentum_only` = `watchlist_momentum` | 같은 scanned activity universe | 미사용 | 사용 | 사용 | 기존 legacy bucket 의 실제 의미 |
| `watchlist_blind_momentum` | 같은 scanned activity universe | 미사용 | 미사용 | 사용 | 같은 scan 후보군 안에서 watchlist/predictor metadata 를 제거한 ablation |

`momentum_only` key 는 DB/CSV/UI 호환을 위해 유지하되 문서와 label 에서는 `watchlist_momentum` legacy alias 로 설명한다.

### Bucket invariant

| Bucket | 반드시 허용되는 정보 | 반드시 제거되는 정보 |
| --- | --- | --- |
| `predictor_weighted` | cutoff 이전 watchlist/predictor snapshot, predictor score/weight, watchlist rank/score, decision timestamp 이전 live activity | cutoff 이후 공시/성과, realized PnL, 미래 quote/bar |
| `momentum_only` = `watchlist_momentum` | 같은 scan universe, watchlist rank/score, decision timestamp 이전 live activity | predictor score/weight 가 entry threshold, sort, sizing 에 미치는 영향 |
| `watchlist_blind_momentum` | 같은 scan universe, pct/volume rank, live activity, stale/halt/spread guard | `predicted=True`, `watchlist_rank`, `watchlist_score`, `watchlist_predicted`, `predicted_watchlist`, predictor/watchlist reason, predictor score/weight 가 entry/sort/sizing 에 미치는 영향 |

### 비교식

- `predictor_weighted - watchlist_momentum` = predictor score/weight incremental effect
- `watchlist_momentum - watchlist_blind_momentum` = watchlist metadata effect within same scan universe
- `predictor_weighted - watchlist_blind_momentum` = predictor stack effect within same scan universe

이 비교는 **진짜 pure momentum 검증이 아니다.** 현재 scanner universe 가 watchlist/live pipeline 에 묶여 있기 때문에 `watchlist_blind_momentum` 은 selection universe 를 공유하는 within-scan ablation 이다. `pure_momentum` 은 Step 0 이후 independent universe/replay provider 가 분리된 v2/v3 에서만 도입한다.

### 해야 할 것

- `PaperTradingRun.bucket` 은 포트폴리오 비교 단위로 유지하고, `strategy_bucket` 은 진입/포지션 내부 원인으로 유지한다.
- 세 포트폴리오를 독립된 cash_balance / positions 로 관리한다.
- 버킷별 activity view/sanitizer 를 둬 `watchlist_blind_momentum` 에 watchlist/predictor metadata 가 들어가지 않게 한다.
- 버킷별 PnL, 거래 수, 승률, 거래별 R 분포를 별도 집계한다.
- 기존 `paper_bucket_trade_diff.csv` 는 호환 파일로 유지하고, 3개 비교쌍은 `paper_bucket_pair_diff.csv` 또는 동등한 decomposition CSV 로 출력한다.
- 세 버킷은 완전 독립이므로 같은 종목을 동시에 보유해도 무방하다.

### Exit Criteria

- [ ] 세 버킷이 독립적으로 동작 (하나의 청산이 다른 쪽에 영향 없음)
- [ ] `watchlist_blind_momentum` 에서 predicted/watchlist metadata 와 predictor score/weight 가 entry/sort/sizing 에 영향을 주지 않음
- [ ] 버킷별 수익/손실 집계가 리포트에 표시됨
- [ ] 백테스팅 종료 후 세 비교쌍의 거래 수준 diff/decomposition 출력 가능

---

## Step 4 — 백테스팅 현실화 (Penny Stock 기준)

**목표**: paper trading 가정을 penny stock 현실에 맞춘다. **Step 0 데이터가 확보된 이후에만** 의미가 있다.

### 세션별 체결 모델 분리

프리장 스프레드는 정규장 대비 3-5배 넓으므로 모델을 세션별로 분리한다.

| 항목 | 현재 | 수정 후 (프리장) | 수정 후 (정규장) |
|------|------|----------------|----------------|
| 슬리피지 | 고정 상수 % | 해당 봉 스프레드 × 1.5 | 해당 봉 스프레드 × 1.0 |
| 진입 가격 | last price | ask 기준 + 추가 틱 | ask 기준 |
| 청산 가격 | last price | bid 기준 - 추가 틱 | bid 기준 |

### 보수적 capacity / participation 리포트

- 기존 volume cap 은 유지하되, 성과 리포트에는 주문/거래별 `shares_pct_of_bar_volume`, `notional_pct_of_bar_dollar_volume`, `estimated_capacity_at_1pct_volume`, `estimated_capacity_at_2pct_volume`, `capacity_limited` 를 남긴다.
- 10%/5%/2% cap 은 체결 가능 상한으로만 보고, 전략 해석에는 1-2% participation 기준의 보수 capacity 시나리오를 함께 본다.
- capacity 가 모두 0 또는 공백인 run 은 유동성 검증이 되지 않은 것으로 보고 performance review 에서 경고 또는 실패 처리한다.

### 보수적 nonlinear slippage proxy

- L2 historical depth 가 없으므로 정밀 order book 시뮬레이션은 보류한다.
- 대신 L1 bid/ask, minute/bar volume, participation rate 를 이용해 `base_slippage + spread_penalty + participation_penalty` 형태의 보수 proxy 를 적용한다.
- participation 이 1%, 2%, 5% 를 넘을수록 penalty 는 선형이 아니라 계단식/제곱형으로 악화시킨다.
- `run_manifest.json` 에 slippage model 과 capacity model 설정을 남겨 사후 해석 가능하게 한다.

### 체결량 cap

- 시총/일거래대금 계층별로 cap 을 차등:
  - 일거래대금 ≥ $10M: 해당 봉 거래량의 **10%** 이하
  - 일거래대금 $2M-$10M: **5%** 이하
  - 일거래대금 < $2M: **2%** 이하
- 주문이 cap 을 넘으면 남은 잔량은 다음 봉으로 이월 (partial fill 시뮬)

### 할트 처리

- Step 0 에서 수집한 halt 이벤트 구간 동안은 체결 불가
- 재개 후 첫 프린트 가격으로 체결하되 추가 슬리피지 **스프레드 × 2** 적용
- halt 시작 시점에 걸려있던 stop order 는 재개가 기준으로 재평가
- historical halt event 가 없으면 minute gap 또는 zero volume stretch fallback 을 사용하되 `inferred=true` 를 남긴다.

### 거래 비용

- 라운드트립 수수료 가정: **최소 $1 / 거래 + 0.1% 약정** 또는 프로커별 실제값 승격
- SEC/TAF 등 미국 주식 부대 수수료 포함
- 페니 주 특성상 수수료 영향이 크므로 KPI 는 수수료 **차감 후** 값으로 보고

### Exit Criteria

- [ ] 동일 전략 replay 시 수정 전보다 수익률이 낮아지는 것 확인 (현실화 검증)
- [ ] 거래량 cap 으로 인해 partial fill 이 발생하는 케이스 1건 이상 로그
- [ ] 스프레드 상위 종목에서 슬리피지가 크게 잡히는 것 확인
- [ ] 세션별(프리장/정규장) 평균 슬리피지 차이가 리포트에 노출됨

---

## Step 5 — KPI / 리포트 강화

**목표**: 결과를 "전략에 edge 가 있는가"로 판단 가능한 지표를 갖춘다. 페니스탁 특성상 기존 6개 지표만으로는 "한 번의 대박에 의존" 같은 함정을 놓친다.

### Setup state 진단 레이어

`score_lt45`, breakeven, cooldown 같은 threshold/parameter ablation 은 보조 진단으로만 유지한다. 다음 핵심 검증은 사람이 보는 setup 판단이 실제 손실/수익을 분리하는지 확인하는 것이다.

- `setup_context` 는 minute bar 만으로 VWAP, distance_to_vwap, premarket high, HOD, opening range high/low, breakout/reclaim/failure, pullback depth, volume expansion/dry-up, higher low/lower high, minutes since HOD, rank persistence, spread/liquidity, dilution/catalyst risk hook 을 만든다.
- `AISetupJudgeV1` 은 당장은 LLM 호출이 아니라 deterministic/rule-backed JSON judge 로 둔다.
- judge 출력은 `setup_state`, `quality`, `risk`, `action_bias`, `confidence`, `invalidation`, `add_condition`, `trim_condition`, `reasons` 를 포함한다.
- setup state taxonomy 는 `DEAD_PUMP`, `WATCH_LEADER`, `VWAP_RECLAIM`, `ORB_BREAKOUT`, `PULLBACK_HOLD`, `FAILED_BREAKOUT`, `STARTER_VALID`, `ADD_VALID`, `TRIM_EXTENSION`, `RUNNER_HOLD`, `EXIT_FAIL` 로 고정한다.
- AI/setup judge 는 상황 해석만 한다. 실제 주문, size, stop, add/trim 실행은 risk/rule engine 이 통제한다.
- L1 bid/ask coverage 가 없으면 실전 체결/stop/slippage 판단으로 해석하지 않는다. minute-only 결과는 setup_state 손익 분리력 sanity/calibration 으로만 본다.

산출물:

- `paper_setup_features.csv`: symbol-minute-bucket 단위 setup context 와 judge JSON
- `paper_setup_state_kpis.csv`: entry setup_state/action_bias 별 closed trade KPI
- `paper_setup_transition_matrix.csv`: entry setup_state -> exit setup_state 전이와 손익/stop-out
- `paper_add_trim_runner_diagnostics.csv`: starter/add/trim/runner/exit bias 관측 빈도와 품질/위험 평균

### 기본 KPI

| 지표 | 의미 |
|------|------|
| Win Rate | 수익 거래 비율 |
| Profit Factor | 총 수익 / 총 손실 |
| Expectancy (E[R]) | 거래당 기대값 (R 단위) |
| Max Drawdown | 최대 낙폭 |
| Sharpe Ratio | 위험 대비 수익 (거래수 부족 시 bootstrap CI 병기) |
| Avg Hold Days / Minutes | 평균 보유 기간 |

### 페니스탁 특화 KPI

| 지표 | 의미 |
|------|------|
| One-winner dependency | 상위 1건 제외 시 총 PnL 변화율 |
| Stop execution slippage | 계획 stop 대비 실제 체결 편차 (페니에서 edge 를 가장 크게 먹는 항목) |
| Time-under-water | 신 고점 사이 최대 경과 기간 |
| Theme concentration | 동일 테마 동시 보유 비율 (상관 위험) |
| Cost-adjusted return | 수수료/부대비용 차감 후 총 수익 |

### Predictor 전용 지표

| 지표 | 의미 |
|------|------|
| Predictor Hit Rate | 후보 중 실제 트리거된 거래의 수익 비율 (분모·분자 정의 문서화) |
| Predictor edge decay | hold day 1 → 2 → 3 로 갈수록 평균 R 이 어떻게 변하는지 |
| Candidate survival rate | 후보가 실제 진입까지 이어진 비율 |

Intraday 전용으로는 `0-5분`, `5-15분`, `15-30분`, `30-60분`, `1-2시간`, `2시간+` bucket 별 `trade_count`, `win_rate`, `avg_r_multiple`, `total_net_pnl` 을 `predictor_weighted`, `momentum_only`, `watchlist_blind_momentum` 별로 비교한다.

### Catalyst KPI split

- trade log 또는 별도 CSV 에 `catalyst_type` 을 남긴다.
- 최소 태그는 `offering_or_dilution`, `reverse_split`, `warrant`, `fda_or_clinical`, `contract_or_business_news`, `sympathy_or_theme`, `social_hype_or_paid_promo`, `no_clear_catalyst` 이다.
- catalyst 별 `closed_trade_count`, `win_rate`, `expectancy_r`, `total_net_pnl`, `avg_hold_minutes` 를 출력한다.

### Tail-risk KPI

- `paper_backtest_kpis.csv` 또는 별도 tail-risk CSV 에 Sortino, Calmar, max consecutive losses, worst trade R, p5/CVaR trade R, median/p90 hold minutes, winner/loser avg hold minutes 를 포함한다.

### Regime / 분포 분석

- `classify_day_regime` 을 이용해 **trend-day vs chop-day** 로 KPI 분해
- 거래별 R 분포를 히스토그램으로 출력 (꼬리 리스크 가시화)
- MDD 는 **Monte Carlo (trade order bootstrap)** 신뢰구간 동반

### 벤치마크

- "프리마켓 gainer top-N 단순 진입" 같은 naive baseline 포트폴리오 1개를 병렬 실행해 모든 비교 리포트에 동반 표시
- 단독 Sharpe 대신 **benchmark 대비 초과 Sharpe** 를 핵심 지표로

### 출력 형태

- 대시보드 내 "백테스팅 KPI" 섹션
- 포트폴리오 A / B / baseline 나란히 비교
- 거래 수준 CSV + regime-split CSV export
- `paper_exit_path_diagnostics.csv`: entry label, exit reason, hold bucket 별 MFE/MAE, R multiple, intrabar stop touch, 1R 도달 여부, breakeven stop 활성화, giveback 을 연결해 stop/exit 손실 경로를 분해한다.

### Exit Criteria

- [ ] 위 기본 KPI + 페니 특화 KPI + predictor KPI 모두 리포트 출력
- [ ] A vs B vs baseline 비교가 한 화면에 보임
- [ ] Sharpe / MDD 에 bootstrap 또는 Monte Carlo 신뢰구간 동반
- [ ] regime-split 리포트가 최소 2개 장세(trend/chop)로 분해됨
- [ ] stop/exit path diagnostics 가 1개월 calibration replay 와 3개월 이상 OOS replay 에서 모두 생성됨
- [ ] `score_lt45` 같은 score filter 결과는 loss-reduction hypothesis 로만 기록되고, OOS/shadow 전에는 live strategy 로 승격되지 않음

---

## Step 6 — Shadow 모드 & Out-of-sample 검증

**목표**: 백테스트 결과가 과적합이 아닌지를 확인한다. 이 단계가 통과해야 LIVE_TRADING 전환 판단이 가능하다.

### Out-of-sample 프로토콜

- 백테스트 기간을 **튜닝 구간 / 검증 구간** 으로 분리
- 파라미터 (k1, k2, threshold, sizing) 은 튜닝 구간에서만 결정
- 검증 구간은 결정된 파라미터로 단 1회 실행, 결과 고정
- 가능하면 **walk-forward** (3개월 튜닝 → 1개월 검증 → 슬라이딩)

### Shadow 모드

- `PremktPredictor` 출력을 매일 저장하는 일일 job 을 구성
- 실매매 연결 없이 forward 2-4주간 예측만 축적
- Shadow 기간의 predictor hit rate / edge decay 가 백테스트 값과 유사한지 확인
- 편차가 크면 과적합 또는 데이터 리크 의심 → 실매매 전환 보류

### Exit Criteria

- [ ] 튜닝/검증 구간 분리 문서화, 검증 구간 결과 1건 확보
- [ ] 최소 2주 이상의 shadow 실행 결과 저장
- [ ] shadow KPI 와 백테스트 KPI 의 편차 리포트

---

## 전체 순서 요약

```
Step 0  (데이터 인프라, 모든 후속 단계의 전제)
  ↓
Step 1 → Step 2 → Step 3   (구조 작업, 순서 지킬 것)
  ↓
Step 4 → Step 5             (품질/측정, Step 3 이후 병렬 가능)
  ↓
Step 6                       (out-of-sample + shadow)
  ↓
3개월 이상 백테스트 + shadow 검증
  ↓
docs/LIVE_TRADING_READINESS_PLAN_KO.md 의 실매매 전환 시작
```

---

## 문서 운영 규칙

- 각 Step 완료 시 해당 Exit Criteria 를 체크하고 `docs/STATUS.md` 를 갱신한다.
- 구현이 이 문서와 달라지면 구현보다 이 문서를 먼저 고친다.
- Step 이 완료되기 전에 다음 Step 을 시작하지 않는다. (단, Step 4/5 는 Step 3 이후 병렬 허용)
- 1개월 결과만 가지고 실매매 전환 판단을 하지 않는다.
