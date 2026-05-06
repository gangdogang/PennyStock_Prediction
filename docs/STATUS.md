# Status

최종 정리일: 2026-05-06

## 현재 capabilities

- `universe -> watchlist -> premarket 분석 -> regular-session 판단` 기본 흐름이 로컬 DB 기준으로 동작한다.
- SEC filing 기반 watchlist 빌드와 `filed_at <= D 08:00 ET` cutoff 적용이 가능하다.
- point-in-time universe snapshot 태깅과 `market_date` 기준 재현 경로가 있다.
- `PremktPredictor` 가 후보 점수, 추천 보유일, 진입 근거를 DB/JSON 으로 저장할 수 있고, 옵션 지정 시 학습된 `train-premkt-model` artifact 점수를 `rule`/`ml`/`blend` 모드로 JSON lineage 와 함께 연결할 수 있다.
- intraday paper engine 이 stale/halt/daily-loss/open-risk 가드레일과 함께 replay/mock-first 실행을 수행한다.
- multiday engine 이 starter, overnight hold, winner add, loser replacement, day2/day3 exit 1차 규칙을 수행한다.
- multiday engine 의 starter/add/hold/replacement 파라미터가 `AppSettings` 와 `.env` override 로 제어되며 기본값 기준 골든 스냅샷이 유지된다.
- KIS live quote timestamp 는 shared helper 기준으로 ET/KST fallback 정규화가 적용되고 `live_market.py` 와 `kis_historical.py` 가 같은 해석 경로를 사용한다.
- live scan/provider 경로는 `automation/logs/live_metrics.jsonl` 기준 JSONL sidecar 로 `quote_age`, `spread`, provider request reject 신호, scan summary 를 남긴다.
- `report-backtest-coverage` 는 coverage report latest JSON 을 `automation/state/backtest_coverage/` 기준으로 정기 산출할 수 있다.
- `capture-kis-l1-window` 는 같은 심볼 집합으로 반복 L1 snapshot 적재를 수행한 뒤 latest L1 coverage report/gate 상태를 갱신할 수 있다.
- `capture-kis-l1-window` 는 KIS WebSocket 동시 구독 한계 대응용 rotation manager 를 사용할 수 있다. 기본값은 resolved universe 전체를 tier1 continuous 로 두어 기존 non-rotation 경로를 유지하고, `--rotation-tier1-size 30 --rotation-tier2-concurrent 10` 같은 설정으로 tier2 rotation 을 켠다.
- `historical_l1_quotes.subscription_continuous` 는 L1 row 가 continuous subscription 구간에서 온 것인지 기록한다. Cost source policy 는 cost-eligible source 라도 `subscription_continuous=True` row 만 비용 근거로 세고, tier2 rotation row 는 diagnostic-only 로 취급한다.
- Backtest L1 coverage report 는 `tier1_continuous_symbol_count`, `tier2_rotation_symbol_count`, `rotation_gap_seconds_p90` 를 기록해 rotation 으로 생긴 누락 구간을 별도 추적할 수 있다.
- Step 0 L1 coverage gate 상태는 `automation/state/backtest_coverage_gate_status.json` latest snapshot 으로 저장되고, 상태 파일의 pass/fail 규칙은 고정되며 CI hard fail 은 Step 0 60% 도달 후 켠다.
- 골든 회귀 기준은 intraday `stop_loss`/`time_stop`, multiday `day2_exit`/`overnight_hold_rejected`/`loser_replacement`, fill-model stop gap 경로까지 고정돼 있다.
- 로컬 품질 게이트는 `scripts/check_quality.sh`, `scripts/check_quality.ps1` 기준으로 골든 회귀 + 전체 `pytest` + coverage gate 상태 요약을 같은 진입점에서 실행할 수 있다.
- GitHub Actions CI 는 `.github/workflows/ci.yml` 기준으로 `.[dev,ui]` 설치 후 같은 품질 게이트를 실행한다.
- intraday 와 multiday 의 `process_market_activity` 가 `services/engine_shared.py:run_step()` 공통 오케스트레이터를 사용한다.
- `services/engine_rules/entry.py`, `exit.py`, `profit.py` 로 intraday 규칙이 분리됐고 multiday도 같은 exit/close 경계를 재사용한다.
- `services/setups/` 는 `SetupRegistry` 와 `LegacyMomentumSetup` 을 제공한다. paper hot loop 는 기본값에서 registry 를 통해 legacy momentum setup 을 dispatch 하되 기존 `engine_rules/entry.py`, `exit.py`, `profit.py` 함수 본문은 그대로 호출한다. `PSR_USE_SETUP_REGISTRY=0` 으로 기존 직접 호출 path 로 되돌릴 수 있다.
- `services/pyramid.py` 는 `PyramidPosition`, leg/schedule/add/trim/runner dataclass, legacy starter-only schedule, aggressive future schedule 정의를 제공한다. paper DB/order/reporting 은 `leg_index`, `setup_id`, `pyramid_state` 컬럼을 갖지만 기본 legacy run 은 `leg_index=0`, `setup_id='legacy_momentum'`, `pyramid_state=NULL` 로 기존 단일 포지션 동작을 유지한다. `PSR_USE_PYRAMID=0` 은 기존 직접 hot loop path 로 되돌린다.
- DB 계층은 `db/connection.py`, `schema.py`, `paper.py`, `execution.py`, `historical.py`, `premkt.py` 중심 패키지로 분할됐고 기존 `from ..db import ...` import 경로는 유지된다.
- CLI 는 `cli/__init__.py` 루트 앱 아래 `premkt.py`, `backtest.py`, `automation.py`, `paper.py`, `broker.py`와 보조 서브앱으로 분할됐고 기존 `psradar <cmd>` 명령 표면은 유지된다.
- `predictor_weighted`, legacy `momentum_only`=`watchlist_momentum`, `watchlist_blind_momentum` 버킷을 독립 포트폴리오로 병렬 비교할 수 있다.
- Windows paper drive 런처는 평가용 기본값으로 `predictor_effect.enabled=true` (`k1=1.0`, `k2=1.0`) 를 프로세스 환경에 명시하고, `launcher_manifest.json` 과 `run_manifest.json` 에 predictor effect/bucket policy 를 남긴다.
- paper trading 결과는 snapshots, orders, positions, KPI CSV, execution quality CSV, `run_manifest.json`, `paper_performance_gate.json` 으로 남길 수 있다.
- paper trade log 는 predictor score/weight fallback 과 prediction source lineage 를 포함하고, performance gate 는 predictor lineage 공백, KPI 분모 불일치, bucket diff 부재를 검사할 수 있다.
- `review-paper-performance` 는 predictor effect disabled, k1/k2 0, enabled-but-identical bucket 결과를 구분해 fail/warning 을 기록한다.
- paper performance 보강 범위는 predictor 수익 판단이 아니라 검증 가능성 강화로 한정한다. 신규 기준 산출물은 보수적 1-2% participation capacity, intraday edge decay, catalyst KPI split, tail-risk KPI, nonlinear L1/minute-volume slippage proxy 이다.
- Windows paper 실행 런처는 run별 export/log/archive/manifest 를 OneDrive 경로에 남기며, 기본 실행은 사용자가 끌 때까지 계속 돈다. 중간 검토는 snapshot archive 로 만들고, 종료 시 final archive 와 DB/WAL 사본을 남긴다.
- 운영 역할은 macOS 에서 코드 수정/테스트/commit 을 하고, Windows 머신은 `C:\Dev\Penny_Stock` 기준 24시간 paper/backtest 서버로 계속 돌리는 방식으로 분리한다. Windows 에서는 `.env`, 로컬 DB, 런타임 산출물만 관리하고 코드 변경은 GitHub 동기화로 받는다.
- snapshot archive manifest 는 실제 DB 파일이 포함된 경우에만 database included 로 기록하고, 빈 database 폴더나 copy 실패는 warning 으로 남긴다.
- KIS historical minute backfill, L1 snapshot archive, coverage report CLI 가 연결돼 있다.
- `build-premkt-training-dataset` CLI 는 `data/backtest_lab/` DB 사본의 `historical_minute_bars` 로 D 08:00 ET 이전 premarket feature 와 cutoff 이후 label 을 분리한 학습용 CSV 를 만들 수 있다.
- `train-premkt-model` CLI 는 1단계 CSV 를 읽어 `label_winner` baseline classifier 를 학습하고, `market_date` 시간순 split 기준 model artifact 와 metrics JSON 을 저장할 수 있다.
- `run-premkt-model-replay` CLI 는 `data/backtest_lab/` DB 사본을 기본 입력으로, point-in-time universe/watchlist 와 cutoff 이전 model feature 만 사용해 과거 날짜 범위를 local-only 로 재생하고 replay 산출물을 `data/backtest_lab/replays/<run_id>/` 아래에 남길 수 있다.
- `run-premkt-model-replay` 는 entry label include/exclude, replay 전용 `k1/k2` override, L1 quote 필수 entry 옵션을 지원하며, trade log 에 entry-time label, exit-time label, fill/slippage/capacity metric, label별 stop-out attribution CSV 를 남긴다.
- `run-premkt-model-replay` 는 stop/exit path diagnostics 를 산출해 stop 전후 MFE/MAE, R multiple, intrabar stop touch, 1R 도달 여부, giveback 을 bucket/label/exit reason/hold bucket 기준으로 분해할 수 있다. replay-only `--breakeven-stop-after-r` 는 close 기준 R multiple 도달 후 stop 을 entry 가격으로 올리는 profit-protection ablation 이며, `--max-entries-per-symbol-per-day` / `--cooldown-after-stop-minutes` 는 stop 이후 재진입 반복을 분리 검증하는 ablation 이다. minute-only 한계 때문에 실전 stop 체결 보장으로 해석하지 않는다.
- `setup_state` v1 은 historical replay 안에서 minute bar 기반 setup_context 를 만들고 deterministic/rule-backed `AISetupJudgeV1` JSON 판단을 기록한다. 출력 state 는 `DEAD_PUMP`, `WATCH_LEADER`, `VWAP_RECLAIM`, `ORB_BREAKOUT`, `PULLBACK_HOLD`, `FAILED_BREAKOUT`, `STARTER_VALID`, `ADD_VALID`, `TRIM_EXTENSION`, `RUNNER_HOLD`, `EXIT_FAIL` 이며 action bias 는 진단용이다.
- `run-premkt-model-replay` 는 setup_state 진단 산출물 `paper_setup_features.csv`, `paper_setup_state_kpis.csv`, `paper_setup_transition_matrix.csv`, `paper_add_trim_runner_diagnostics.csv` 를 추가로 남긴다. trade log 에도 entry/exit setup state, quality, risk, action_bias 를 붙여 setup 판단이 손익/stop-out 을 분리하는지 볼 수 있다.
- `audit-premkt-entry-signal` CLI 는 여러 replay output directory 의 `paper_trade_log.csv` 와 `paper_setup_features.csv` 를 읽어 특정 entry label/setup_state 조합의 월별/심볼별/일별 손익, top symbol/date 제거 후 손익, 1R 도달 feature bucket 을 JSON/CSV 로 산출할 수 있다. 기본 감사 대상은 `OPENING_RANGE_CANDIDATE + STARTER_VALID` 이다.
- `run-falsification-audit` CLI 는 feature tuning 전에 governance/budget, data inventory, point-in-time/survivorship blocker, L1/minute spread cost audit, same-universe random-time null benchmark, strategy trade-log 기반 same-universe random-entry null benchmark, fixed/ATR/structure stop geometry, benchmark suite status, final gate summary 를 `data/backtest_lab/research_runs/<run_id>/` 에 남긴다. 이 gate 가 `PASS` 되기 전에는 entry/setup/score/filter/stop/sizing tuning 을 금지한다.
- `run-falsification-audit` 의 cost audit 는 source policy 를 적용한다. cost evidence 는 `kis_l1_snapshot` 또는 명시적 full NBBO/SIP 계열 source 만 허용하고, `alpaca_iex_historical_quotes` / `alpaca_iex_diagnostic` / `alpaca_iex_*` 는 diagnostic-only 로 분리한다. report 는 source별 전체/eligible/diagnostic count 와 excluded source 목록을 machine-readable 로 남긴다.
- `audit-pit-universe-reconstruction` CLI 는 historical minute bar 날짜별로 exact point-in-time universe 가 있는지, 없으면 bar-derived diagnostic universe 정도만 가능한지 JSON/MD/CSV 로 판정한다. diagnostic bar universe 는 lookahead/adverse-selection 위험 때문에 edge 판단 blocker 를 해소하지 않는다.
- `tag-pit-universe-scan` CLI 는 명시적 기존 scan 을 point-in-time 으로 태그하고 PIT-vs-current diff 를 남길 수 있다. D 08:00 ET cutoff 이후 생성 scan 은 기본 거부하며, override 는 diagnostic plumbing 용도에 한정한다.
- `backfill-alpaca-iex-quotes` CLI 는 Alpaca historical IEX quote 를 `historical_l1_quotes.source='alpaca_iex_historical_quotes'` 로 저장한다. 이 경로는 strategy entry schedule 주변 window 또는 symbol/date range 를 지원하지만 cost PASS 근거로 쓰지 않는다.
- `archive-nasdaq-symbol-directory` CLI 는 현재 Nasdaq Symbol Directory(`nasdaqlisted.txt`, `otherlisted.txt`)를 raw/report artifact 로 저장하고, 명시적 `--allow-current-date` 일 때만 forward PIT `scan_runs/universe` 를 기록한다. 2025년 과거 PIT 복원으로 취급하지 않는다.
- `backfill-sec-filings-pit` CLI 는 SEC EDGAR submissions 를 D 08:00 ET cutoff 기준으로 eligible/diagnostic-after-cutoff 로 나누고, PIT scan/filings artifact 를 남긴다.
- `backfill-finra-otc-daily-list` CLI 는 FINRA OTC Daily List JSON/CSV 를 symbol change/name change/deleted/split/dividend/corporate action staging artifact 로 저장한다. `--write-database` 는 최소 `corporate_actions` inventory 에만 insert 하며, current-only rows 는 historical survivorship blocker 를 자동 해소하지 않는다.
- `audit-research-data-coverage` CLI 는 falsification audit 전 날짜별 minute bars, PIT universe, cost-eligible L1, diagnostic-only Alpaca IEX, minute spread source split, corporate action coverage, SEC cutoff coverage 를 JSON/CSV/MD 로 요약하고 shortfall 섹션을 자동 포함한다.
- `report-coverage-shortfall` CLI 는 Step 0 blocker 를 binary `BLOCKED` 가 아니라 minute bar 개월 수, cost-eligible overlap %, corporate action 개월 수의 부족분으로 정량화한다. 산출물은 `operational_planning_only_not_decision_grade` stamp 를 포함하며, vendor 비용은 사용자가 입력한 월 단가만 사용한다.
- `audit-universe-tradability` CLI 는 replay/watchlist/scanner universe 의 listing exchange 를 PIT universe, Nasdaq Symbol Directory, 선택적 yfinance cache 순서로 확인해 KIS tradable/untradable/unknown 비율을 JSON/CSV/MD 로 산출한다. untradable+unknown 이 30% 이상이면 `universe_kis_untradable_pct_high` blocker 로 기록된다.
- `run-benchmark-suite` CLI 와 `services/benchmark_suite.py` 는 strategy expectancy 를 cash, same-universe random entry, random time within day, top-gainer naive, volume-leader naive, opposite-side diagnostic 6개 benchmark 대비 incremental 로 비교할 entry-event/report 배선을 제공한다. 실제 execution 전 cost-eligible source policy 를 먼저 적용하며, BLOCKED 상태에서는 benchmark entry generation 도 거부한다.
- `run-multiday-catalyst-replay` CLI 와 `services/multiday_catalyst_replay.py` 는 길 B 사전 준비용 D~D+5 catalyst replay scaffold 를 제공한다. SEC filing + PIT universe + KIS tradability filter 로 entry event 를 만들고, volume exhaustion / follow-on filing / structure stop / max holding day exit 를 EOD bar 기반으로 검증 산출물에 남긴다.
- `evaluate-kis-quote-consolidation` CLI 는 `historical_l1_quotes.source='kis_l1_snapshot'` 의 bid/ask exchange 다양성, active-hours update frequency, spread distribution 을 검사해 `automation/state/source_validation/latest_kis_consolidation.json` verdict 를 갱신한다.
- falsification cost source policy 는 최신 KIS consolidation verdict 가 `nbbo_consolidated` 일 때만 `kis_l1_snapshot` 을 cost-eligible 로 허용한다. verdict 가 `single_venue_proxy` 이면 KIS L1 rows 는 diagnostic-only 로 강등된다.
- `ExternalDataValidator` 는 신규 외부 quote source 를 cost source policy 에 넣기 전 NBBO consolidation, market-hours coverage, spread sanity, license redistribution/cost-evidence 허용 여부를 검증한다. `config/cost_source_policy.json` 이 동적 whitelist/diagnostic/rejected source 를 관리하며, 파일이 없으면 기본 policy 를 생성하고 파일이 깨졌으면 기존 기본 whitelist 로 fail-safe fallback 한다.
- `backfill-ibkr-historical-quotes` CLI 는 optional `ib_insync` extra 로 IBKR historical BID_ASK ticks 를 `historical_l1_quotes.source='ibkr_nbbo'` 로 적재한다. personal-use / redistribution 금지 license 를 source validation JSON 과 cost source policy 에 기록하고, NBBO consolidation/spread/coverage 검증을 통과한 경우에만 cost-eligible 후보로 등록한다.
- Step 5 historical replay 검증은 1개월 calibration 과 3개월 이상 out-of-sample replay 산출물을 `evaluate-premkt-replay` / `run-premkt-validation-plan` 으로 평가해 `evaluation_report.json` 의 coverage, leakage, 비용/체결, bucket 비교, decision gate 를 분리 기록한다.
- historical replay 산출물은 `replay_grade` stamp 를 가진다. `stamp_replay_run` 은 falsification `decision_gate=PASS`, KIS universe tradability blocker 없음, cost source policy 위반 없음, KIS 사용 시 `nbbo_consolidated` verdict 를 모두 만족할 때만 `decision_grade=True` 로 기록하고, KPI/diagnostic CSV 첫 줄에는 같은 grade/reason 주석을 붙인다.
- historical replay 는 날짜별 minute bar 와 모델 scoring feature 를 symbol별 반복 조회하지 않고 `market_date + symbol IN (...)` bulk load 로 읽은 뒤, prepared bar cursor 와 누적 volume/dollar volume 으로 simulated time 을 진행한다. model scorer 는 run 안에서 재사용하고, ML/blend scoring 은 replay bar cache 를 재사용해 같은 날짜의 minute bar 재조회 비용을 줄인다. setup_state 는 VWAP/HOD/opening-range 누적 지표를 prepared series prefix 값으로 계산해 반복 과거 봉 스캔을 피한다. non-blind bucket 은 activity deep-copy 를 생략하고, 전략 entry/exit/stop/sizing 규칙은 변경하지 않는다. `progress.json` 에 날짜별 elapsed, loaded bar rows, loaded symbols, simulated time count 를 남긴다.
- KIS mock broker execution 경로가 `providers/broker.py`, `providers/kis_mock_broker.py`, `services/broker_execution.py` 기준으로 분리돼 있다.
- broker execution 결과는 `execution_orders`, `execution_positions`, `execution_accounts` 테이블에 저장된다.
- Streamlit 대시보드는 v1 cleanup 기준으로 `ui/app.py` 를 bootstrap/sidebar/data load/tab routing 중심으로 줄이고, 공통 layout helper, `ui/pages/` 탭 렌더러, 첫 화면 view model 로 분리한다.
- snapshot HTML, AI supervisor, launcher 스크립트가 같은 저장소 구조를 기준으로 동작한다. macOS `.command` 파일은 루트가 아니라 `launchers/macos/` 아래에 모으고, Windows 런처는 `launchers/windows/` 아래에 둔다.
- 문서 진입 기준은 `README.md -> docs/STATUS.md -> docs/STEP_PROGRESS_KO.md` 로 줄였고, 백테스트/운영/매매/live 문서는 작업 종류가 맞을 때만 추가로 읽는다. `archive/`, `sample_outputs/`, `automation/inbox/` markdown 은 기본 읽기 대상이 아니다.

## 현재 한계

- 2026-04-21 이전에 생성된 paper 성능평가 CSV는 predictor lineage 와 KPI 분모가 불완전할 수 있으므로 새 export 로 다시 생성해야 한다.
- `BACKTEST_ROADMAP_KO.md` Step 0 기준 KIS historical minute/L1 coverage 60% gate 는 아직 통과하지 못했다.
- 임의의 과거 날짜 D 전체를 재현할 만큼 장기 archive 적재량이 아직 부족하다.
- live observability 는 아직 JSONL sidecar 수준이며 대시보드 집계, 알람, broker execution reject telemetry 분리는 남아 있다.
- stale/halt/trade-condition hard gate 는 live smoke 기준으로 다시 검증해야 한다.
- `report_builder.py`, `ai_supervisor.py`, `providers/live_market.py` 는 여전히 단일 파일이 커서 변경 범위가 넓다.
- `SetupRegistry` 는 현재 legacy momentum wrapper 만 포함한다. Pyramid state machine 인프라는 있으나 실제 add/trim/runner schedule 을 켜는 새 setup 은 아직 없다. 기존 score/predictor archive 정리는 후속 Step 으로 분리한다.
- `premkt_historical_replay.py` 는 bulk load/cursor/scoring 분리 후에도 아직 큰 orchestration 파일이다. 후속 정리는 export/report aggregation, diagnostics writer, CLI-facing runner facade 순서로 작게 나눈다.
- Streamlit UI cleanup v1 은 구조 분리와 첫 화면 정보 구조 보존이 목표이며, 디자인 polish 와 비즈니스 로직 변경은 후속 작업으로 둔다.
- 큰 service 파일 cleanup 은 `report_builder.py` 를 1순위로 두고 facade/API 를 유지한 채 payload loading, markdown export, snapshot HTML renderer, HTML formatting helper 순서로 나눈다.
- KIS mock broker execution 은 `trade-plan` 기반 반자동 검증 범위만 지원하고 auto loop, reconciliation, recovery runbook 이 없다.
- full tape/websocket 기반 실시간 엔진이 아니며 기본 구조는 계속 `replay/mock-first` 성격이 강하다.
- KIS WebSocket rotation manager 는 subscription plan, stamp, coverage/cost policy 배선까지만 구현됐다. 실제 KIS WebSocket connection 변경은 하지 않았고 테스트도 mock 기반이다.
- 기존 `momentum_only` 버킷은 pure momentum 이 아니라 watchlist universe 와 watchlist metadata 를 유지한 watchlist-aware momentum 이었다. 현재 scanner input universe 자체가 watchlist/live pipeline 에 묶여 있으므로 이 이름만으로 predictor/watchlist/momentum alpha 를 분리했다고 해석하면 안 된다.
- 현재 단계에서 가능한 비교는 동일 scanned activity universe 안에서 metadata 를 제거하는 `watchlist_blind_momentum` 방식의 within-scan ablation 이며, 진짜 `pure_momentum` 은 independent universe/replay provider 가 분리된 뒤에만 도입한다.
- Step 4/5 리포트는 존재하지만 Step 0 coverage 와 shadow/out-of-sample 검증 전에는 live 판단 근거가 될 수 없다.
- predictor effect disabled 또는 Step 0 coverage 60% 미만 run 은 predictor edge 판단 근거로 쓰지 않는다.
- PremktPredictor 학습 준비 1일차 산출물은 성능 판별이 아니라 누수 없는 학습 데이터셋 생성 기반이다. cutoff 이후 minute bar 가 없으면 row 는 유지하고 label 컬럼은 비워 둔다.
- PremktPredictor 학습 준비 3단계는 모델 점수 연결이며, trading 성능 판단은 아직 아님. 모델 점수에 필요한 historical minute feature 가 부족하면 rule score 로 fallback 하고 JSON lineage 에 이유를 남긴다.
- historical replay smoke 또는 calibration 결과는 최종 성능 판단이 아니다. 과거 백테스트가 좋아도 곧바로 실매매 판단이 아니며, 좋은 OOS 결과의 최대 판정은 `promising_needs_shadow` 다.
- `replay_grade.decision_grade=False` 산출물은 UI/report 에서 명시적으로 표시되며 edge 판단 근거로 쓰지 않는다. 기존 run 에 falsification audit, universe tradability audit, cost policy/KIS consolidation 근거가 없으면 보수적으로 false stamp 가 정상이다.
- `score_lt45` 는 live strategy 가 아니라 2025년 6월 sanity replay 와 2025년 4~5월 backward robustness 에서 손실 감소 가능성을 본 frozen hypothesis 다. Step 0 coverage, 고정 파라미터 OOS, shadow 검증 전에는 실매매 진입 필터로 해석하지 않는다.
- `setup_state` v1 도 live strategy 가 아니다. AI/setup judge 는 상황 해석만 하며 주문은 계속 risk/rule engine 이 통제한다. 현재 구현은 L1 부재와 minute-only replay 한계 때문에 setup state 의 손익 분리력 검증용 진단 배선으로만 해석한다.
- `audit-premkt-entry-signal` 결과도 strategy approval 이 아니다. 이 리포트는 특정 조합이 심볼/날짜 집중 착시인지, 1R 도달 조건이 분리되는지 보는 감사 산출물이며 L1 없는 replay 에서는 계속 sanity evidence 로만 본다.
- fixed 5% stop 기준의 `reached_1r` / `stop_before_1r` 는 ATR/volatility/spread 에 오염될 수 있다. trade-path 분석은 ATR-normalized R, structure-stop R, spread-adjusted R 을 병기하기 전까지 edge label 로 해석하지 않는다.
- 페니스탁 intraday 모멘텀의 기본 prior 는 음수다. structural edge, universe adverse selection, survivorship, L1/spread cost, execution latency, holding horizon, null benchmark 를 통과하지 못하면 feature 탐색 결과를 strategy 후보로 승격하지 않는다.
- 2026-04-30 이전 historical replay CSV 의 `analysis_label` 은 EXIT row 에서 청산 시점 label 로 기록됐을 수 있다. `WAIT_PULLBACK` 등 entry label attribution 은 새 replay 산출물의 `entry_analysis_label` / `exit_analysis_label` 기준으로 다시 봐야 한다.
- L2 historical depth 가 없으므로 order book 시뮬레이터는 만들지 않는다. 현재 현실화는 L1 quote, minute volume, halt/resume 상태를 이용한 보수적 proxy 로만 해석한다.
- 현재 최우선 blocker 는 overnight falsification gate 미통과 상태다. 결과가 `FAIL` 이면 hypothesis 를 폐기하고, `BLOCKED` 이면 데이터/coverage/survivorship/L1 cost/benchmark 보강만 허용한다.
- 2026-05-06 Phase 0 falsification blocker 보강 MVP 로 Polygon 제외 무료 데이터 배선을 추가했다. Cost source policy, Alpaca IEX diagnostic quote importer, Nasdaq forward PIT archiver, SEC EDGAR PIT backfill, FINRA OTC Daily List staging, coverage audit CLI 가 들어갔지만 이는 edge 승인 근거가 아니라 blocker 를 더 명확하게 드러내는 배선이다.
- 2026-05-06 Coverage shortfall quantifier 를 추가했다. 이 산출물은 길 A/B/C 의 운영 계획 비교용이며 decision grade, edge judgment, strategy approval 로 해석하지 않는다.
- 2026-05-06 Universe tradability audit 를 추가했다. 새 listing source 는 기본 diagnostic-only 이며, KIS 거래 가능성은 backtest transferability blocker 로만 사용한다. yfinance lookup 은 기본 비활성이고 cache 를 지정한 경우에만 보조 조회한다.
- 2026-05-06 KIS quote consolidation verdict 전에는 `kis_l1_snapshot` 을 cost-eligible 최상위 근거로 신뢰하지 않는다. 기존 historical row 의 `bid_exchange` / `ask_exchange` 는 NULL 이며, KIS 응답에서 venue 필드가 확인되지 않으면 `single_venue_proxy` 또는 `insufficient_evidence` 로 남는 것이 정상이다.
- IBKR historical NBBO 는 본인 계정 personal-use license 로만 사용한다. source validation 결과와 summary JSON 은 내부 검증용 metadata 이며 원 quote 데이터 재배포 근거가 아니다.
- strategy trade-log 기반 `same_universe_random_entry` 는 비용 관측치가 strategy market_date 와 겹칠 때만 실행한다. 다른 날짜의 L1/minute spread 를 current-cost fallback 으로 써서 2025 replay 를 통과시키지 않는다.
- 2026-05-05 로컬 smoke `run-falsification-audit --run-id smoke_local --null-sample-count 20` 결과는 `BLOCKED` 다. 현재 `data/backtest_lab/` DB 기준 blocker 는 6개월 minute bar 부족, point-in-time scan 부재, corporate_actions 부재, same-universe null 불가, benchmark suite 미완성, spread sample <1000 이다.
- point-in-time universe blocker 의 첫 실행 단계는 `audit-pit-universe-reconstruction` 으로 날짜별 복구 가능성을 분리하는 것이다. exact PIT 없는 날짜를 bar-derived diagnostic 으로 임시 통과시키지 않는다.
- 2026-05-05 `audit-pit-universe-reconstruction --run-id pit_smoke_local --min-bars-per-symbol 30` smoke 결과는 `diagnostic_reconstruction_possible` 이다. 현재 DB에는 exact PIT 가 없고, 2026-04-17 historical bar 기반 diagnostic universe 1건만 가능하다.
- 2026-05-05 `run-falsification-audit --strategy-trade-log sample_outputs/paper_trading/paper_trade_log.csv --strategy-bucket momentum_only` smoke 결과에서 `same_universe_random_entry` 는 `point_in_time_universe_missing` 으로 blocked 됐다. trade log parser/CLI 는 동작하지만 exact PIT 전에는 benchmark blocker 를 해소하지 않는다.
- 2026-05-06 Windows `matched_june_2025_sec_universe` 확인 결과 `historical_minute_bars=2,284,272` 이지만 `minute_spread_rows=0`, `minute_bid_ask_rows=0`, `historical_l1_quotes=0` 이다. 따라서 2025년 6월 matched random-entry 는 `cost_distribution_missing` 이 정상 blocker 이며, 이 DB로는 net expectancy 또는 live feasibility 를 판정하지 않는다.
- 2026-05-06 Spec 8 benchmark suite 는 코드 배선만 완료했다. cost-eligible source 확보 전에는 `run-benchmark-suite` 가 `decision_grade=False` / `blocked` 산출물을 남기는 것이 정상이며, benchmark 결과를 strategy approval 로 해석하지 않는다.
- 2026-05-06 Spec 9 multi-day catalyst replay 는 scaffolding 이며 길 A 실패 시 길 B 로 넘어가기 위한 사전 배선이다. falsification gate PASS 없이 생성된 산출물은 `decision_grade=False` 가 정상이고, EOD/yfinance representative cost 는 multi-day horizon 검토용 cost eligibility 로만 해석한다.

## OneDrive 기존 run 인벤토리

2026-04-30 확인 기준 `OneDrive/Penny_Stock_Runs` 에는 Windows 에서 생성된 2025년 6월 historical replay 와 2026년 4월 paper run 이 있다. 이 산출물은 구버전 코드 결과이므로 실성능 평가가 아니라 참고/경고 신호로만 본다.

1개월 historical replay:

| 폴더 | 기간 | 완료 거래일 | k1/k2 | 해석 |
| --- | --- | ---: | --- | --- |
| `calibration_2025_06_rule_k0` | 2025-06-02~2025-06-30 | 20 | 0/0 | predictor 와 momentum 동일, 약 -$4,856 |
| `june_2025_momentum_conservative` | 2025-06-02~2025-06-30 | 20 | 0/0 | `calibration_2025_06_rule_k0` 와 동일 계열 |
| `june_2025_sec_universe` | 2025-06-02~2025-06-30 | 20 | 1/1 | predictor -$1,217, momentum -$1,787 |
| `june_2025_sec_strict_dv100k_top30` | 2025-06-02~2025-06-30 | 20 | 1/1 | predictor -$7,811, momentum -$6,800 |
| `june_2025_sec_strict_conservative` | 2025-06-02~2025-06-30 | 20 | 1/1 | predictor -$3,446, momentum -$3,039 |

미완성/보조 산출물:

- `calibration_2025_06_rule_k025` 는 2025-06-24 에서 `running`, 이후 날짜가 `pending` 으로 남아 summary/CSV 가 없어 완료 run 으로 보지 않는다.
- `coverage_checks/2025-06-02_gate.json` 기준 L1 premarket coverage 는 0% 로 gate failed 다.
- 2개월 historical replay 산출물은 `OneDrive/Penny_Stock_Runs` 안에서 확인되지 않았다.
- `paper_runs/`, `paper_24h_runs/` 는 2026년 4월 live/paper 산출물이며 closed trade 수가 작고 `paper_performance_gate.json` 이 `edge_judgment_allowed=false` 를 기록하므로 predictor edge 판단에 쓰지 않는다.

2026-05-01 추가 확인 기준 `replay_after_809a57c_2025_06/` 에는 `4ff3214` 코드로 다시 생성한 2025년 6월 replay 가 있다. 이 산출물은 `entry_analysis_label` / `exit_analysis_label` 이 분리된 새 schema 이지만, 여전히 L1 bid/ask coverage warning 이 있어 minute-only sanity 결과로만 본다.

| 폴더 | 기간 | k1/k2 | 핵심 결과 |
| --- | --- | --- | --- |
| `baseline_k0` | 2025-06-02~2025-06-30 | 0/0 | predictor=momentum -$6,946, blind -$5,179 |
| `k025_size0` | 2025-06-02~2025-06-30 | 0.25/0 | `baseline_k0` 와 동일, threshold 완화 효과 없음 |
| `k025_size025` | 2025-06-02~2025-06-30 | 0.25/0.25 | predictor -$7,864, sizing 증폭이 손실 확대 |
| `conditional_only` | 2025-06-02~2025-06-30 | 0/0 | predictor=momentum -$5,426 |
| `no_opening_range` | 2025-06-02~2025-06-30 | 0/0 | predictor=momentum -$5,426, blind -$5,179 |
| `require_l1_smoke` | 2025-06-02~2025-06-03 | 0/0 | L1 필수 진입 시 거래 0건 |

새 run 기준 손실 가설:

- `OPENING_RANGE_CANDIDATE` 제거는 baseline 대비 손실을 줄였지만 전체 결과는 여전히 음수다.
- `k1=0.25` 는 진입 수/손익을 바꾸지 않아 현재 후보 대부분이 이미 threshold 위에 있었던 것으로 본다.
- `k2=0.25` 는 거래 수와 승률 변화 없이 predictor bucket 손실만 키웠으므로 edge 검증 전 size amplification 은 계속 보류한다.
- baseline predictor/momentum 에서 stop loss 는 482/569건이고, `OPENING_RANGE_CANDIDATE` entry stop 비율은 93.8%, `CONDITIONAL_ENTRY` entry stop 비율은 77.9% 다.
- exit 시점 label 은 `WAIT_PULLBACK` 과 `OPENING_RANGE_CANDIDATE` 에 손실이 집중되고, `CONDITIONAL_ENTRY` exit label 은 상대적으로 양호하다. 다만 minute-only stop/exit 구조라 매매 품질 결론은 금지한다.
- 새 replay 산출물은 label 전이, quick stop, 심볼 손실 집중도, 보유시간 bucket 을 보기 위해 `paper_entry_exit_label_matrix.csv`, `paper_stop_out_diagnostics.csv`, `paper_symbol_loss_concentration.csv`, `paper_hold_bucket_kpis.csv` 를 추가로 저장한다. 이 네 파일은 성능 판정용이 아니라 다음 ablation 우선순위 결정용이다.
- 다음 손실 감소 실험을 위해 `run-premkt-model-replay` 는 replay-only 옵션 `--entry-score-upper-bound`, `--min-entry-time`, `--exit-label`, `--breakeven-stop-after-r`, `--max-entries-per-symbol-per-day`, `--cooldown-after-stop-minutes` 을 지원한다. 현재 6월 sanity 결과의 우선 가설은 `OPENING_RANGE_CANDIDATE` 제외 후에도 남는 `CONDITIONAL_ENTRY` early stop, `analysis_score >= 4.5` 과열 진입, `WAIT_PULLBACK` 전환 방치, +1R 이후 stop-out/giveback 방치, stop 이후 같은 종목 반복 진입이다.
- `score_lt45` 는 `analysis_score < 4.5` 과열 진입 회피 가설로 고정한다. 현재 해석은 손실 감소 ablation 후보이며, predictor edge 또는 live 전략 승인 신호가 아니다.
- stop/exit path diagnostics 는 stop 발생 전후의 MFE/MAE, R multiple, intrabar low stop touch, session_end giveback 을 보기 위한 원인 분해 산출물이다. 성능 판정이 아니라 `entry filter -> stop/exit structure -> OOS` 순서의 다음 실험 우선순위 결정에만 사용한다.

## 2026-05-05 ablation 결과 (Windows replay_outputs)

`STARTER_VALID` + `--min-entry-time 09:00` + `k1=0,k2=0` 조건 기준:

| run | 기간 | 거래수 | 총손익 | 1R도달 |
| --- | --- | ---: | --- | --- |
| `validation_min0900_jun` | 2025-06-02~06-30 | 550 | -$1,836 | 42% |
| `breakeven_jun` | 2025-06-02~06-30 | 1,031 | -$12,246 | 41% |
| `no_conditional_jun` | 2025-06-02~06-30 | 164 | **+$2,448** | 47% |
| `no_conditional_aprmay` | 2025-04-01~05-31 | 636 | -$6,984 | 41% |
| `no_conditional_may` | 2025-05-01~05-31 | 314 | -$1,618 | 39% |

핵심 발견:

- `1R 도달 여부가 손익을 결정`한다. 1R 도달 시 수익, 미달 시 손실 구조가 전 기간 일관됨.
- `CONDITIONAL_ENTRY` 를 제외(no_conditional)하면 June 2025 기준 +$2,448 로 수익 전환됐으나 April-May 에서 -$6,984 로 실패. 3개월 합산 -$4,536.
- `breakeven_stop` 단독 사용은 역효과. 1R 도달 후 포지션 청산 → 같은 종목 당일 재진입 반복으로 거래 수 2배, 손실 급증.
- June 플러스는 시장 레짐 효과로 판단. April 트럼프 관세 급락 구간에서 전략 붕괴 확인.
- entry_label별 1R 도달율(June 기준): `OPENING_RANGE_CANDIDATE` 50%, `NEWS_CHECK_FIRST` 45%, `CONDITIONAL_ENTRY` 39%.
- exit_setup_state 기준 `TRIM_EXTENSION` 청산 시 수익, `RUNNER_HOLD` 청산 시 손실 패턴 확인.
- 현재 backtest_lab DB 범위(2025-04-01~06-30)에서 이 가설은 통계적으로 robust 하지 않음.
- `STARTER_VALID + ORC`, `STARTER_VALID + 09:00 + no_conditional`, `breakeven_stop` 단독 가설은 현재 기준 strategy 후보가 아니라 `rejected / regime-dependent / diagnostic-only` 로 취급한다.

## 다음 우선순위

- `BACKTEST_ROADMAP_KO.md` Step -1 성능평가 배선 검증은 완료됐다.
- PremktPredictor 학습 준비 4단계는 point-in-time historical replay runner 구현으로 시작했다. 핵심 원칙은 과거 날짜 D의 판단에 D 이후 데이터와 cutoff 이후 feature 를 쓰지 않는 것이다.
- 즉시 순서는 `audit-pit-universe-reconstruction` 으로 exact PIT 가능 날짜와 diagnostic-only 날짜를 분리한 뒤, `run-falsification-audit` overnight run 으로 governance/data-bias-cost/null/stop-geometry/benchmark blocker 를 산출하고, 결과를 `PASS / FAIL / BLOCKED` 로 판정하는 것이다. `PASS` 전에는 setup_state, entry label, score cutoff, stop, sizing, add/trim tuning 을 하지 않는다.
- Windows historical replay 는 기존 손실 attribution 산출물을 재사용하지 말고 `k1=0,k2=0` baseline 과 label ablation 을 2026-05-01 이후 코드로 다시 생성한다.
- 장시간 historical replay/backtest 는 Windows 24시간 서버에서 실행하고, 맥북은 코드 수정과 짧은 smoke/quality gate 용도로 쓴다.
- `k2` size amplification 과 winner add/scaling-in 은 entry/stop 구조가 음수 expectancy 를 벗어난 뒤에만 켠다. 현재는 손실을 키우는지 줄이는지 평가할 KPI 배선부터 먼저 만든다.
- 3개월 기준은 실제 시간을 기다리는 운영이 아니라 과거 데이터 재생 기준이며, 개발 루프는 2일 smoke -> 5-10일 sanity -> 1개월 calibration -> 3개월 이상 out-of-sample 순서로 진행한다.
- Step 0 coverage 60% gate 와 L1 archive 적재는 병행 과제다. L1 없는 replay 는 계속 smoke/sanity 판정으로만 유지한다.
- Step 6은 완료됐고 multiday 설정은 `AppSettings` 와 `.env.example` 로 승격됐으며 env override 회귀 테스트와 골든 검증을 통과했다.
- Step 7은 완료됐고 KIS live timestamp 정규화와 live JSONL observability 추가 후 전체 `173 passed` 를 확인했다.
- Step 8은 완료됐고 coverage report latest JSON 과 gate 상태 파일이 기준 경로에 고정됐으며 전체 `176 passed` 를 확인했다.
- Step 9는 완료됐고 intraday `time_stop`, multiday `overnight_hold_rejected`/`loser_replacement` 골든 스냅샷을 추가한 뒤 전체 `179 passed` 를 확인했다.
- Step 10은 완료됐고 로컬 품질 게이트 스크립트와 GitHub Actions CI 를 추가한 뒤 전체 `184 passed` 를 확인했다.
- Step 11은 완료됐고 `services/setups/` registry + legacy momentum wrapper 를 추가했다. 기본 ON / `PSR_USE_SETUP_REGISTRY=0` fallback 모두 골든 bit-exact 이며 `./scripts/check_quality.sh` 기준 `354 passed, 1 skipped` 를 확인했다. Synthetic 1-day hot-loop 비교는 registry ON 평균 0.9450s, fallback OFF 평균 0.9574s 로 5% slowdown 조건을 넘지 않았다.
- Step 12는 완료됐고 `services/pyramid.py` state machine, DB/reporting metadata, legacy schedule hook, `PSR_USE_PYRAMID=0` fallback 을 추가했다. Legacy paper path 는 기본 `LEGACY_SCHEDULE` 로 단일 포지션 동작을 유지하며 실제 multi-leg add/trim enable 은 후속 setup 작업으로 남긴다. `./scripts/check_quality.sh` 기준 골든 8개 포함 전체 `383 passed, 1 skipped` 를 확인했다.
- Step 0 보강으로 `capture-kis-l1-window` 반복 archive runner 를 추가했고 관련/전체 테스트 `185 passed` 를 확인했다.
- 이후에는 Step 0 coverage 확보와 shadow/out-of-sample 검증 순으로 다시 돌아간다.
- UI cleanup v1 이후 남은 구조 정리 후보는 `report_builder.py`, `providers/live_market.py`, `ai_supervisor.py`, `paper_reporting.py`, `market_activity.py` 순서다. `ai_supervisor.py` 는 현재 freshness 테스트 실패 원인을 먼저 분리한 뒤 착수한다.
- live readiness Phase 1 은 timestamp/observability 기반은 들어왔지만 live smoke 와 hard gate 검증 전까지는 여전히 문서/검증 단계로 유지한다.
- 골든 스냅샷과 `tests/golden/` 은 의도된 diff 가 아니면 건드리지 않는다.
- 각 Step 완료 시 이 문서를 먼저 갱신하고, 그 다음 진행 기록과 관련 문서를 맞춘다.
- 새 `.md` 를 추가하기보다 기존 기준 문서에 흡수한다. 과거 기록은 active docs 에 오래 두지 말고 `archive/` 로 보낸 뒤 기본 읽기 대상에서 제외한다.
