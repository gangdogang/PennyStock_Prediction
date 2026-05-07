# Step Progress

최종 갱신일: 2026-05-07

## 운영 원칙

- 한 번에 하나의 Step 만 진행한다.
- 각 Step 완료 시 `docs/STATUS.md` 를 먼저 갱신한다.
- 설계, 우선순위, 성능평가 기준, 구현 방향이 바뀌면 작업 전에 관련 `.md` 를 먼저 갱신한다.
- 성능평가 산출물은 코드 수정 근거가 되므로, 해석 결과와 다음 작업은 `docs/STATUS.md` 또는 `docs/BACKTEST_ROADMAP_KO.md` 에 남긴 뒤 구현한다.
- 구조와 문서가 어긋나면 구현보다 문서를 먼저 고친다.
- 기본 문서 읽기 범위는 `AGENTS.md`, `README.md`, `docs/STATUS.md`, `docs/STEP_PROGRESS_KO.md` 로 제한하고, `BACKTEST_ROADMAP_KO.md` 등은 작업 종류가 맞을 때만 추가로 연다.
- 병렬화 가능한 조사와 구현은 토큰 비용을 신경 쓰지 말고 서브에이전트를 최대한 적극적으로 사용한다.
- 서브에이전트는 읽기 전용 조사, 경로 확인, 테스트 범위 분리, 구현 분리처럼 책임이 명확한 단위로 나눠 쓴다.
- Overnight falsification gate 가 `PASS` 되기 전에는 feature/parameter tuning 을 하지 않는다. 허용되는 작업은 runbook 실행, 산출물 해석, blocker 문서화, 데이터 coverage/point-in-time/survivorship/L1 cost/benchmark 보강뿐이다.
- active 경로인 intraday paper engine 과 KIS mock broker execution 경로는 의도 없이 흔들지 않는다.
- `tests/test_regression_golden.py`, `tests/golden/` 은 의도된 diff 가 아니면 변경하지 않는다.

## 진행 현황

| Step | 상태 | 메모 |
| --- | --- | --- |
| -1 | DONE | predictor score/weight lineage, KPI 분모 정의, bucket divergence smoke, run manifest, performance gate 고정. `./scripts/check_quality.sh` 기준 `199 passed`. 후속 보강으로 bucket semantic split / within-scan ablation v1 적용 |
| 1 | DONE | `STATUS.md` 축약, handoff 중복 제거, archive 이동, live readiness 경로 교정 완료 |
| 2 | DONE | `process_market_activity` 공통 오케스트레이터 도입, 골든 포함 전체 테스트 통과 |
| 3 | DONE | `engine_rules/` 패키지로 entry/exit/profit 분리, `paper_trading.py` 891줄, 전체 테스트 통과 |
| 4 | DONE | `db/` 패키지로 분할, `db/__init__.py` re-export 유지, 전체 테스트 통과 |
| 5 | DONE | `cli/` 패키지 + Typer 서브앱 분할, 기존 `psradar <cmd>` 명령 집합 유지, 전체 테스트 통과 |
| 6 | DONE | multiday 상수를 `AppSettings`/`.env.example`로 승격, 골든 bit-exact 유지, env override 테스트 추가, 전체 `171 passed` 확인 |
| 7 | DONE | KIS live timestamp shared helper 도입, live JSONL observability 추가, 관련/전체 테스트 `173 passed` 확인 |
| 8 | DONE | coverage report JSON/latest export 와 gate 상태 파일 추가, 관련/전체 테스트 `176 passed` 확인 |
| 9 | DONE | intraday `time_stop`, multiday `overnight_hold_rejected`/`loser_replacement` 골든 추가, 관련/전체 테스트 `179 passed` 확인 |
| 10 | DONE | GitHub Actions CI + 로컬 quality gate 스크립트 추가, 관련/전체 테스트 `184 passed` 확인 |
| 11 | DONE | `services/setups/` SetupRegistry + LegacyMomentumSetup 추가. 기본 ON dispatch 와 `PSR_USE_SETUP_REGISTRY=0` fallback 모두 골든 bit-exact 유지, `./scripts/check_quality.sh` 기준 `354 passed, 1 skipped` 확인 |
| 12 | DONE | `services/pyramid.py` PyramidPosition state machine, DB/reporting metadata, LegacyMomentumSetup `LEGACY_SCHEDULE`, `PSR_USE_PYRAMID=0` fallback 추가. 실제 multi-leg add/trim enable 은 후속 setup 작업으로 분리. `./scripts/check_quality.sh` 기준 `383 passed, 1 skipped` |

## 현재 메모

- Step -1 완료 후에는 새 paper export 를 다시 생성해 이전 CSV의 predictor lineage 공백 여부를 재검증한다.
- UI cleanup v1 은 별도 구조 정리 작업으로 진행한다. 범위는 `ui/app.py` 를 page config/sidebar/data load/tab routing 중심으로 축소하고, 공통 style/layout helper, `ui/pages/` 탭 렌더러, 첫 화면 view model 을 분리하는 데 한정한다.
- 첫 화면의 종합 상황판 정보 구조와 Streamlit 동작은 최대한 유지한다. 큰 service 파일 정리는 `report_builder.py` facade 유지형 분할을 다음 후보로 둔다.
- Step -1 후속 보강으로 `momentum_only` 를 pure momentum 으로 오해하지 않게 하고, `watchlist_blind_momentum` 을 추가해 동일 scan universe 안에서 watchlist metadata 효과를 분리하는 v1 을 적용했다.
- 이 보강은 Step 0 coverage 작업과 병행 가능한 얇은 semantic split 이지만, 성능평가 해석의 전제라 Step 0 장기 적재 루프를 재개하기 전에 먼저 고정한다.
- 2026-04-23 보강으로 Windows paper drive 런처의 평가 run 은 predictor effect 를 기본 활성화하고, disabled/identical bucket 결과는 review gate 에서 구분한다.
- `capture-kis-l1-window` 는 반복 적재 후 coverage report/gate 를 갱신하며, L1 timestamp drift/snapshot_date mismatch 는 gate failure 로 남긴다.
- 현재 작업 범위는 Step 4/5 의 측정/현실화 보강이다. capacity, intraday decay, catalyst split, nonlinear slippage proxy, halt freeze/resume penalty, tail-risk KPI 를 추가하되 live 전환과 predictor 파라미터 튜닝은 하지 않는다.
- PremktPredictor 학습 준비 1일차는 성능 판별이 아니라 학습 데이터셋 생성 기반이다. `build-premkt-training-dataset` 은 cutoff 이전 minute bar 만 feature 로 쓰고, cutoff 이후 bar 로 label 을 만들며, cutoff 이후 bar 가 없으면 label 을 빈 값으로 남긴다.
- PremktPredictor 학습 준비 2단계는 baseline model training / validation signal 확인으로 완료했다. trading 성능 판단이 아니다.
- PremktPredictor 학습 준비 3단계는 모델 점수 연결로 완료했다. `run-premkt-predictor --model-path ... --score-mode rule|ml|blend --ml-weight ...` 가 optional scoring 을 수행하고, 기본값은 기존 rule-based 점수와 동일하다. 이 단계는 trading 성능 판단은 아직 아님.
- PremktPredictor 학습 준비 4단계는 point-in-time historical replay runner 구현이다. `run-premkt-model-replay` 는 실시간 하루하루를 기다리지 않고 과거 DB 사본으로 `predictor_weighted`, `momentum_only`=`watchlist_momentum`, `watchlist_blind_momentum` 을 같은 기간에서 smoke 비교하는 경로다.
- replay 의 핵심 원칙은 미래 데이터 누수 금지다. D일 predictor score 는 D cutoff 이전 feature 만 쓰고, intraday decision 은 simulated time 이전/현재 bar 만 본다.
- Step 5 historical replay 검증은 구현된 replay 산출물을 대상으로만 적용한다. `evaluate-premkt-replay` 는 1개월 calibration 과 3개월 이상 out-of-sample 산출물의 `replay_summary.json`, `run_manifest.json`, KPI/거래 CSV 를 읽어 `evaluation_report.json` 을 만든다.
- 현재 smoke/sanity/calibration 결과는 최종 성능 판단이 아니다. 과거 백테스트가 좋아도 곧바로 실매매 판단이 아니며, 좋은 OOS 결과의 최대 판정은 `promising_needs_shadow` 다.
- 2026-04-30: historical replay 의 EXIT row `analysis_label` 을 entry-time label 로 고정하고 `exit_analysis_label` 을 별도 기록하게 했다. 기존 OneDrive run 의 label별 손실 attribution, 특히 `WAIT_PULLBACK` 손실은 청산 시점 label 이 섞였을 수 있으므로 새 코드로 다시 산출해야 한다.
- 2026-04-30: `run-premkt-model-replay` 에 entry label include/exclude, replay 전용 `--predictor-k1/--predictor-k2`, `--require-l1-quotes-for-entries` 옵션과 `paper_entry_label_kpis.csv` 를 추가했다.
- 2026-04-30: `OneDrive/Penny_Stock_Runs` 기존 산출물 인벤토리를 `docs/STATUS.md` 에 기록했다. 확인된 1개월 replay 는 2025년 6월 20거래일이며 2개월 historical replay 는 발견하지 못했다.
- 2026-05-01: `replay_after_809a57c_2025_06/` 새 schema replay 를 확인했다. `entry_analysis_label` / `exit_analysis_label` 분리는 정상이고, `require_l1_smoke` 는 거래 0건으로 L1 부재를 확인했다. `k1=0.25,k2=0` 은 baseline 과 동일했고 `k2=0.25` 는 손실을 키웠으므로 size amplification 은 계속 보류한다.
- 2026-05-01: replay 결과에 `paper_entry_exit_label_matrix.csv`, `paper_stop_out_diagnostics.csv`, `paper_symbol_loss_concentration.csv`, `paper_hold_bucket_kpis.csv` 를 추가했다. 목적은 손실 메꾸기 순서를 entry/exit label 전이, quick stop, 심볼 집중도, 보유시간 bucket 기준으로 쪼개는 것이다.
- 2026-05-01: `run-premkt-model-replay` 에 replay-only ablation 옵션 `--entry-score-upper-bound`, `--min-entry-time`, `--exit-label` 을 추가했다. 목적은 2025년 6월 손실 가설인 과열 score 진입, 09:30 직후 진입, `WAIT_PULLBACK` 전환 방치를 실제 재생으로 분리 검증하는 것이다.
- 2026-05-01: stop/exit path diagnostics 구현은 성능 판정이 아니라 손실 경로 분해 배선으로 본다. 이 산출물은 entry filter ablation 과 stop/exit 구조 ablation 의 우선순위를 정하는 용도다.
- 2026-05-01: `score_lt45` 해석은 `analysis_score < 4.5` 과열 진입 회피에 따른 손실 감소 hypothesis 로 동결한다. live strategy 가 아니며, 고정 파라미터 3개월 이상 OOS 와 shadow 검증 전에는 실매매 판단에 쓰지 않는다.
- 2026-05-01: historical replay 속도 개선으로 minute bars/model scoring bulk fetch, `(market_date, symbol, bar_at)` index, prepared bar cursor, activity copy 제거, 날짜별 timing/progress metric 을 추가했다. 생성물인 `src/penny_stock_radar.egg-info/` 와 로컬 캐시/중복 `.gitkeep 2` 도 정리했다. 이번 변경은 성능 최적화/구조 분리이며 entry/exit/stop/sizing 의사결정 로직은 변경하지 않는다.
- 2026-05-05: historical replay 추가 속도 개선으로 model scorer 를 run 단위로 재사용하고, ML/blend scoring 이 이미 로드한 replay bar cache 를 써서 날짜별 minute bar 중복 조회를 피하게 했다. `setup_state` 는 prepared series 의 prefix VWAP/HOD/opening-range 지표를 사용해 전 종목/전 bucket 진단 중 반복 과거 봉 스캔을 제거했다. `scripts/psradar`, `scripts/check_quality.sh`, macOS `.command` 진입점도 문서와 맞게 복구했다.
- 2026-05-05: markdown 기본 읽기 범위를 줄였다. 새 컨텍스트는 `AGENTS.md`, `README.md`, `docs/STATUS.md`, `docs/STEP_PROGRESS_KO.md` 만 기본으로 읽고, 백테스트/운영/매매/live 문서는 작업 종류가 맞을 때만 추가로 읽는다. `archive/`, `sample_outputs/`, `automation/inbox/` markdown 은 기본 읽기 대상이 아니다.
- 2026-05-05: 루트에 흩어져 있던 macOS `.command` 런처를 `launchers/macos/` 로 옮기고, README/운영 가이드/런처 문서/테스트/UI 안내 문구를 새 경로에 맞췄다. 루트는 핵심 문서, 설정, 패키지 메타 중심으로 유지한다.
- 2026-05-01: `run-premkt-model-replay` 에 close 기준 R multiple 도달 후 stop 을 entry 가격으로 올리는 replay-only `--breakeven-stop-after-r` 옵션을 추가했다. 이 옵션은 +1R 이후 stop-out/giveback 감소 가설 검증용이며, L1 없는 minute-only 환경에서는 실전 stop 체결 보장으로 해석하지 않는다.
- 2026-05-01: breakeven stop 결과는 giveback 은 줄였지만 stop 이후 같은 종목 반복 진입이 늘어 총손실이 커질 수 있음을 보여줬다. 이를 분리하기 위해 replay-only `--max-entries-per-symbol-per-day`, `--cooldown-after-stop-minutes` 옵션을 추가했다.
- 2026-05-02: threshold/parameter ablation 을 계속 늘리지 않고 `setup_state` v1 진단 레이어를 추가했다. `SetupContextBuilder` 는 minute bar 로 VWAP/reclaim/HOD/ORH/pullback/volume/rank/liquidity feature 를 만들고, deterministic `AISetupJudgeV1` 은 taxonomy/action_bias JSON 판단을 기록한다. replay 는 `paper_setup_features.csv`, `paper_setup_state_kpis.csv`, `paper_setup_transition_matrix.csv`, `paper_add_trim_runner_diagnostics.csv` 를 남긴다.
- 2026-05-05: `STARTER_VALID` + `min-entry-time 09:00` + `CONDITIONAL_ENTRY` 제외 조합으로 June 2025 +$2,448 수익 전환을 확인했으나, April-May 2025 에서 -$6,984 로 실패해 3개월 합산 -$4,536. 시장 레짐 의존성이 확인됐으며 이 가설은 robust 하지 않음. `breakeven_stop` 단독 사용은 재진입 반복으로 역효과 확인. 1R 도달 여부가 손익을 결정하는 구조는 전 기간 일관됨.
- 2026-05-05: `audit-premkt-entry-signal` 을 추가했다. 여러 replay output directory 를 받아 기본 `OPENING_RANGE_CANDIDATE + STARTER_VALID` 조합의 월별/심볼별/일별 손익, top symbol/date 제거 후 손익, 1R 도달 feature bucket 을 JSON/CSV 로 감사한다. 목적은 June 양수 조합이 심볼/날짜 집중 착시인지 먼저 확인하는 것이다.
- 2026-05-05: 전략/성과 분석 기준을 structural-edge-first 로 상향했다. `STARTER_VALID + ORC`, `STARTER_VALID + 09:00 + no_conditional`, `breakeven_stop` 단독은 strategy 후보가 아니라 `rejected / regime-dependent / diagnostic-only` 로 취급한다.
- 2026-05-05: `run-falsification-audit` 를 추가했다. 이 runner 는 feature tuning 없이 governance/budget, data inventory, point-in-time/survivorship/L1 cost blocker, same-universe random-time null benchmark, fixed/ATR/structure stop geometry, benchmark suite status, final gate summary 를 `data/backtest_lab/research_runs/<run_id>/` 에 기록한다.
- 2026-05-05: 로컬 smoke `run-falsification-audit --run-id smoke_local --null-sample-count 20` 는 `BLOCKED` 를 반환했다. 이는 현재 DB 상태에서 정상 결과이며, edge 판단은 금지된다. 같은 변경 기준 `./scripts/check_quality.sh` 는 `268 passed, 1 skipped` 를 확인했다.
- 2026-05-05: `audit-pit-universe-reconstruction` 를 추가했다. 이 명령은 날짜별 exact PIT universe 존재 여부와 bar-derived diagnostic universe 가능성을 분리해 기록한다. bar-derived diagnostic 은 null 배선 smoke 용도일 뿐 survivorship/adverse-selection blocker 를 해소하지 않는다.
- 2026-05-05: `tag-pit-universe-scan` 를 추가했다. 명시적 scan_id 만 PIT 로 태그할 수 있고, scan 생성 시각이 D 08:00 ET 이후이면 기본 거부한다. 이 경로는 forward archive salvage 용도이며 과거 2025 replay 의 PIT 를 날조하지 않는다.
- 2026-05-05: PIT audit smoke 는 현재 DB에서 `diagnostic_reconstruction_possible` 을 반환했다. exact PIT 는 0건이고, 2026-04-17 bar-derived diagnostic universe 만 가능하다. 같은 변경 기준 `./scripts/check_quality.sh` 는 `272 passed, 1 skipped` 를 확인했다.
- 2026-05-05: `run-falsification-audit` 에 `--strategy-run-dir` / `--strategy-trade-log` / `--strategy-bucket` 를 추가하고, strategy entry schedule 기반 `same_universe_random_entry` benchmark 를 구현했다. exact PIT universe, same-minute bar overlap, cost sample 이 없으면 current universe fallback 없이 machine-readable `blocked` reason 을 남긴다. 같은 변경 기준 `./scripts/check_quality.sh` 는 `273 passed, 1 skipped` 를 확인했다.
- 2026-05-06: Windows `matched_june_2025_sec_universe` 산출물에서 2025년 6월 DB 의 L1/minute spread 관측치가 0개임을 확인했다. `same_universe_random_entry` 는 전체 DB 비용 샘플이 있더라도 strategy market_date 와 겹치지 않으면 `cost_distribution_date_overlap_missing` 으로 blocked 되게 강화했다. `./scripts/check_quality.sh` 기준 `274 passed, 1 skipped`.
- 2026-05-06: Phase 0 falsification blocker 보강 MVP 를 추가했다. Cost source policy 는 Alpaca IEX 를 diagnostic-only 로 차단하고, Alpaca IEX quote importer / Nasdaq forward PIT archiver / SEC EDGAR PIT filing backfill / FINRA OTC Daily List staging / `audit-research-data-coverage` CLI 를 구현했다. 이 변경은 데이터 coverage, PIT, survivorship, cost realism, benchmark plumbing 만 다루며 setup_state/entry label/score cutoff/stop/sizing/add/trim tuning 은 건드리지 않았다. 전체 테스트는 `.venv/bin/python -m pytest -q tests` 기준 `292 passed, 1 skipped`.
- 2026-05-06: Step 11 setup registry foundation 을 추가했다. `LegacyMomentumSetup` 은 기존 `engine_rules/entry.py`, `exit.py`, `profit.py` 함수 본문을 이동하지 않고 wrapper 로만 호출하며, paper hot loop 는 기본값에서 `SetupRegistry` 를 통해 legacy setup 을 dispatch 한다. `PSR_USE_SETUP_REGISTRY=0` fallback 은 기존 직접 호출 path 를 유지한다. `./scripts/check_quality.sh` 는 `354 passed, 1 skipped`, synthetic 1-day hot-loop 비교는 registry ON 평균 0.9450s / fallback OFF 평균 0.9574s, 4 bucket x 100 sample dispatch mismatch 0건을 확인했다.
- 2026-05-06: Step 12 PyramidPosition state machine 인프라를 추가했다. `services/pyramid.py` 는 starter/add/trim/runner schedule 과 aggressive future schedule 을 정의하고, legacy setup 은 `LEGACY_SCHEDULE` 을 반환한다. paper DB 는 `paper_orders.leg_index`, `paper_orders.setup_id`, `paper_positions.pyramid_state` 를 idempotent migration 으로 추가하며, legacy run 은 `leg_index=0`, `setup_id='legacy_momentum'`, `pyramid_state=NULL` 을 유지한다. `PSR_USE_PYRAMID=0` 은 기존 직접 hot loop path 로 fallback 한다. 실제 multi-leg add/trim/runner enable 과 새 setup 추가는 후속 Step 으로 남긴다. `./scripts/check_quality.sh` 는 골든 8개 포함 전체 `383 passed, 1 skipped` 를 확인했다.
- 2026-05-06: Spec 1 Universe Tradability Audit 를 추가했다. `audit-universe-tradability` 는 replay/watchlist/scanner universe 를 KIS tradable/untradable/unknown 으로 분류하고 JSON/CSV/MD 산출물에 `decision_grade` / `grade_reason` 을 기록한다. `run-falsification-audit` 는 untradable+unknown 30% 이상일 때 `universe_kis_untradable_pct_high` blocker 를 추가하고, `audit-research-data-coverage` 는 `kis_tradable_universe_pct` 를 JSON 에 포함한다. 관련 테스트 `22 passed`, 전체 `.venv/bin/python -m pytest -q tests` 기준 `299 passed, 1 skipped`.
- 2026-05-06: Spec 2 KIS Quote Consolidation Check 를 추가했다. `historical_l1_quotes` 에 nullable `bid_exchange` / `ask_exchange` 를 추가하고, `evaluate-kis-quote-consolidation` 이 KIS L1의 exchange 다양성/update frequency/spread 분포를 verdict JSON 으로 남긴다. `run-falsification-audit` cost source policy 는 latest verdict 가 `nbbo_consolidated` 일 때만 `kis_l1_snapshot` 을 cost-eligible 로 허용하며, `single_venue_proxy` verdict 는 diagnostic-only 로 강등한다.
- 2026-05-06: Spec 3 External Data Source Validator 를 추가했다. `ExternalDataValidator` 는 외부 source 의 NBBO consolidation, market-hours coverage, spread sanity, license redistribution/cost-evidence 허용 여부를 검증하고, 통과 결과만 `config/cost_source_policy.json` 의 cost-eligible whitelist 에 등록한다. `run-falsification-audit` 는 하드코딩 whitelist 대신 policy 파일을 동적으로 읽되, policy 누락/손상 시 기존 기본 whitelist 로 fallback 한다.
- 2026-05-06: Spec 4 KIS WebSocket Rotation Manager 를 추가했다. `KisWebSocketRotationManager` 는 tier1 continuous / tier2 rotation 배정과 rotation gap p90 측정을 담당하고, `capture-kis-l1-window` 는 기본 non-rotation 호환을 유지하면서 옵션으로 30+10 rotation capture 를 수행할 수 있다. `historical_l1_quotes.subscription_continuous` 를 추가해 tier2 rotation row 를 cost diagnostic-only 로 분리했다.
- 2026-05-06: Spec 5 IBKR Historical Data Integration 를 추가했다. `backfill-ibkr-historical-quotes` 는 optional `ib_insync` extra 로 historical BID_ASK ticks 를 30분 청크 단위로 `historical_l1_quotes.source='ibkr_nbbo'` 에 적재하고, duplicate row / rate-limit retry / failed chunk summary 를 기록한다. personal-use / redistribution 금지 license 는 `ExternalDataValidator` 에 등록하며 validator 통과 시 cost source policy 의 cost-eligible 후보가 된다.
- 2026-05-06: Spec 6 Coverage Shortfall Quantifier 를 추가했다. `CoverageShortfallReport` 는 minute bar, cost-eligible overlap, corporate action target 대비 부족분과 archive/vendor path 추정치를 기록하고, `report-coverage-shortfall` 및 `audit-research-data-coverage` shortfall 섹션으로 노출된다. 이 산출물은 `operational_planning_only_not_decision_grade` 로 stamp 하며 decision grade 로 쓰지 않는다.
- 2026-05-06: Spec 7 Decision-Grade Stamp on Replay Outputs 를 추가했다. `ReplayGrade` / `stamp_replay_run` 이 replay 산출물에 `replay_grade` 를 붙이고, falsification PASS + universe/cost/KIS consolidation 조건을 모두 통과하지 못하면 KPI/diagnostic CSV 주석과 UI/report gate 에 `decision_grade=False` 를 노출한다. 기존 replay 매매 로직은 변경하지 않았고 전체 테스트는 `.venv/bin/python -m pytest -q tests` 기준 `326 passed, 1 skipped`.
- 2026-05-06: Spec 8 Benchmark Suite 코드 배선을 추가했다. `BenchmarkSuiteRunner` 는 6개 benchmark entry event 를 생성하되 cost-eligible source policy 가 BLOCKED 이면 benchmark generation 을 거부하고 `ReplayGrade(decision_grade=False)` report/CSV 를 남긴다. 새 테스트와 backtest CLI 회귀는 `.venv/bin/python -m pytest -q tests/test_benchmark_suite.py tests/test_cli_backtest.py` 기준 `19 passed`.
- 2026-05-06: Spec 9 Multi-day Catalyst Replay scaffolding 을 추가했다. `run-multiday-catalyst-replay` 는 intraday paper engine 을 건드리지 않고 SEC filing/PIT universe/KIS tradability 기반 entry generator 와 multi-day exit policy 를 별도 서비스로 실행하며, `tests/golden/multiday_catalyst/` 에 별도 golden snapshot 을 둔다. 전체 테스트는 `.venv/bin/python -m pytest -q tests` 기준 `334 passed, 1 skipped`.
- 2026-05-07: Hugging Face CryptoSpartan 1m bars read-only audit 를 추가했다. `audit-hf-1m-bars` 는 `PSR_HF_STOCKS_1M_PATH` / `PSR_DATA_ROOT` / repo-local fallback 순서로 parquet 경로를 찾고, Polars lazy scan 으로 gross OHLCV sanity/coverage 후보 지표를 JSON/MD 로 남긴다. 산출물은 `decision_grade=False`, `cost_grade=none` 으로 고정한다.
- 2026-05-07: Setup-first 전환은 setup backtest 가 아니라 `setup_alerts` diagnostic bus 와 setup taxonomy v0 부터 시작한다. v0 는 `AfternoonVwapReclaim`, `Day2MorningPanic`, `FirstGreenDayContinuation` 후보/blocked 사유를 분리하고, 기존 paper/replay 주문 로직은 변경하지 않는다.
- 2026-05-07: `build-setup-alerts-from-features` CLI, `setup_alerts` DB table, CSV/JSON/summary export, setup taxonomy/classifier 테스트를 추가했다.
- 2026-05-07: 데이터 다운로드 완료 즉시 실행할 수 있도록 `segment-hf-candidate-days` CLI 를 추가했다. HF 1m parquet 를 ticker-day 단위로 분해해 low-price universe, early volume momentum, afternoon runner, posthoc high-move label, coverage/concentration gate 를 산출하며, 결과는 계속 `decision_grade=False`, `cost_grade=none` 이다. Windows full-file groupby 메모리 압박을 피하기 위해 기본 3개월 chunk 로 처리하고 `--chunk-months 1` 옵션을 지원한다.
- 2026-05-07: `segment-hf-candidate-events` CLI 를 추가했다. ticker-day 가 아니라 09:45/10:30/14:00/15:30 ET event-time 후보를 만들고, 후보 생성에는 event 시점까지의 OHLCV 만 사용한다. event 시점 최신 bar 가 기본 2분보다 오래된 stale 후보는 제외하며, forward 30/60/120분 regular-session gross path 는 결과 진단 컬럼으로만 붙인다.
- 2026-05-07: `audit-hf-candidate-event-robustness` CLI 를 추가했다. 연도별 event CSV 를 합쳐 top1/top5/top10 ticker 제거 전후 event 수, concentration, event-time/time-bucket 분포, forward outcome 변화를 먼저 반증한다. 여기서 붕괴하면 setup backtest 금지와 더 큰 OHLCV/NBBO 데이터 전환을 우선한다.
- 같은 기준 전체 검증은 `.venv/bin/python -m pytest -q tests` 기준 `401 passed, 1 skipped`.
- 다음 우선순위는 Windows 에서 `audit-hf-candidate-event-robustness` 를 실행한 뒤 top ticker 제거 후에도 분포가 살아남는지 해석하는 것이다. 그래도 setup backtest 는 아직 금지이며, `PASS` 전에는 setup_state filter tuning, entry label tuning, score cutoff tuning, stop/sizing/add/trim tuning, fixed parameter OOS, shadow/live paper 검증으로 넘어가지 않는다.
- 그 다음 구현 우선순위는 Step 0 coverage 60% gate 확보와 6-12개월 이상 archive 적재다.
- 3개월 검증은 실제 시간을 기다리는 방식이 아니라 과거 데이터 재생 기준이다. 구현 루프는 2일 smoke, 5-10일 sanity, 1개월 calibration, 3개월 이상 out-of-sample 순으로 빠르게 반복한다.
- Step 단위 커밋 원칙을 유지하고, 한 커밋에 여러 Step 을 섞지 않는다.

## KIS timestamp timezone 검증 메모

- 결론: ET
- 근거 요약: `historical_l1_quotes` 최신 archive row 기준 `TSLA | quote_at=2026-04-18T08:59:57-04:00 | created_at=2026-04-18T13:37:33.504548+00:00 | abs delta=37.6분` 으로 2시간 이내였고, 13~14시간 오프셋은 관찰되지 않았다. `live_market.py:547-548,665-666` 와 `kis_historical.py:338-339` 가 같은 `dymd/dhms` 해석 경로를 사용하며, `kis_client.py` 는 field 의미를 해석하지 않는 공통 HTTP wrapper 다.
- 남은 위험: 경험적 증거 기반 확정이라 KIS 응답 포맷이 바뀌면 `capture_l1_quotes` 의 120분 drift canary 가 먼저 경고해야 한다.

2026-04-19 리뷰 Finding 전부 close (4 HIGH / 4 MEDIUM / 4 LOW) — pytest 195 passed
