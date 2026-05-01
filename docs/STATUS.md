# Status

최종 정리일: 2026-05-01

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
- Step 0 L1 coverage gate 상태는 `automation/state/backtest_coverage_gate_status.json` latest snapshot 으로 저장되고, 상태 파일의 pass/fail 규칙은 고정되며 CI hard fail 은 Step 0 60% 도달 후 켠다.
- 골든 회귀 기준은 intraday `stop_loss`/`time_stop`, multiday `day2_exit`/`overnight_hold_rejected`/`loser_replacement`, fill-model stop gap 경로까지 고정돼 있다.
- 로컬 품질 게이트는 `scripts/check_quality.sh`, `scripts/check_quality.ps1` 기준으로 골든 회귀 + 전체 `pytest` + coverage gate 상태 요약을 같은 진입점에서 실행할 수 있다.
- GitHub Actions CI 는 `.github/workflows/ci.yml` 기준으로 `.[dev,ui]` 설치 후 같은 품질 게이트를 실행한다.
- intraday 와 multiday 의 `process_market_activity` 가 `services/engine_shared.py:run_step()` 공통 오케스트레이터를 사용한다.
- `services/engine_rules/entry.py`, `exit.py`, `profit.py` 로 intraday 규칙이 분리됐고 multiday도 같은 exit/close 경계를 재사용한다.
- DB 계층은 `db/connection.py`, `schema.py`, `paper.py`, `execution.py`, `historical.py`, `premkt.py` 중심 패키지로 분할됐고 기존 `from ..db import ...` import 경로는 유지된다.
- CLI 는 `cli/__init__.py` 루트 앱 아래 `premkt.py`, `backtest.py`, `automation.py`, `paper.py`, `broker.py`와 보조 서브앱으로 분할됐고 기존 `psradar <cmd>` 명령 표면은 유지된다.
- `predictor_weighted`, legacy `momentum_only`=`watchlist_momentum`, `watchlist_blind_momentum` 버킷을 독립 포트폴리오로 병렬 비교할 수 있다.
- Windows paper drive 런처는 평가용 기본값으로 `predictor_effect.enabled=true` (`k1=1.0`, `k2=1.0`) 를 프로세스 환경에 명시하고, `launcher_manifest.json` 과 `run_manifest.json` 에 predictor effect/bucket policy 를 남긴다.
- paper trading 결과는 snapshots, orders, positions, KPI CSV, execution quality CSV, `run_manifest.json`, `paper_performance_gate.json` 으로 남길 수 있다.
- paper trade log 는 predictor score/weight fallback 과 prediction source lineage 를 포함하고, performance gate 는 predictor lineage 공백, KPI 분모 불일치, bucket diff 부재를 검사할 수 있다.
- `review-paper-performance` 는 predictor effect disabled, k1/k2 0, enabled-but-identical bucket 결과를 구분해 fail/warning 을 기록한다.
- paper performance 보강 범위는 predictor 수익 판단이 아니라 검증 가능성 강화로 한정한다. 신규 기준 산출물은 보수적 1-2% participation capacity, intraday edge decay, catalyst KPI split, tail-risk KPI, nonlinear L1/minute-volume slippage proxy 이다.
- Windows paper 실행 런처는 run별 export/log/archive/manifest 를 OneDrive 경로에 남기며, 기본 실행은 사용자가 끌 때까지 계속 돈다. 중간 검토는 snapshot archive 로 만들고, 종료 시 final archive 와 DB/WAL 사본을 남긴다.
- snapshot archive manifest 는 실제 DB 파일이 포함된 경우에만 database included 로 기록하고, 빈 database 폴더나 copy 실패는 warning 으로 남긴다.
- KIS historical minute backfill, L1 snapshot archive, coverage report CLI 가 연결돼 있다.
- `build-premkt-training-dataset` CLI 는 `data/backtest_lab/` DB 사본의 `historical_minute_bars` 로 D 08:00 ET 이전 premarket feature 와 cutoff 이후 label 을 분리한 학습용 CSV 를 만들 수 있다.
- `train-premkt-model` CLI 는 1단계 CSV 를 읽어 `label_winner` baseline classifier 를 학습하고, `market_date` 시간순 split 기준 model artifact 와 metrics JSON 을 저장할 수 있다.
- `run-premkt-model-replay` CLI 는 `data/backtest_lab/` DB 사본을 기본 입력으로, point-in-time universe/watchlist 와 cutoff 이전 model feature 만 사용해 과거 날짜 범위를 local-only 로 재생하고 replay 산출물을 `data/backtest_lab/replays/<run_id>/` 아래에 남길 수 있다.
- `run-premkt-model-replay` 는 entry label include/exclude, replay 전용 `k1/k2` override, L1 quote 필수 entry 옵션을 지원하며, trade log 에 entry-time label, exit-time label, fill/slippage/capacity metric, label별 stop-out attribution CSV 를 남긴다.
- `run-premkt-model-replay` 는 stop/exit path diagnostics 를 산출해 stop 전후 MFE/MAE, R multiple, intrabar stop touch, 1R 도달 여부, giveback 을 bucket/label/exit reason/hold bucket 기준으로 분해할 수 있다. replay-only `--breakeven-stop-after-r` 는 close 기준 R multiple 도달 후 stop 을 entry 가격으로 올리는 profit-protection ablation 이며, `--max-entries-per-symbol-per-day` / `--cooldown-after-stop-minutes` 는 stop 이후 재진입 반복을 분리 검증하는 ablation 이다. minute-only 한계 때문에 실전 stop 체결 보장으로 해석하지 않는다.
- Step 5 historical replay 검증은 1개월 calibration 과 3개월 이상 out-of-sample replay 산출물을 `evaluate-premkt-replay` / `run-premkt-validation-plan` 으로 평가해 `evaluation_report.json` 의 coverage, leakage, 비용/체결, bucket 비교, decision gate 를 분리 기록한다.
- historical replay 는 날짜별 minute bar 와 모델 scoring feature 를 symbol별 반복 조회하지 않고 `market_date + symbol IN (...)` bulk load 로 읽은 뒤, prepared bar cursor 와 누적 volume/dollar volume 으로 simulated time 을 진행한다. non-blind bucket 은 activity deep-copy 를 생략하고, 전략 entry/exit/stop/sizing 규칙은 변경하지 않는다. `progress.json` 에 날짜별 elapsed, loaded bar rows, loaded symbols, simulated time count 를 남긴다.
- KIS mock broker execution 경로가 `providers/broker.py`, `providers/kis_mock_broker.py`, `services/broker_execution.py` 기준으로 분리돼 있다.
- broker execution 결과는 `execution_orders`, `execution_positions`, `execution_accounts` 테이블에 저장된다.
- Streamlit 대시보드는 v1 cleanup 기준으로 `ui/app.py` 를 bootstrap/sidebar/data load/tab routing 중심으로 줄이고, 공통 layout helper, `ui/pages/` 탭 렌더러, 첫 화면 view model 로 분리한다.
- snapshot HTML, AI supervisor, launcher 스크립트가 같은 저장소 구조를 기준으로 동작한다.

## 현재 한계

- 2026-04-21 이전에 생성된 paper 성능평가 CSV는 predictor lineage 와 KPI 분모가 불완전할 수 있으므로 새 export 로 다시 생성해야 한다.
- `BACKTEST_ROADMAP_KO.md` Step 0 기준 KIS historical minute/L1 coverage 60% gate 는 아직 통과하지 못했다.
- 임의의 과거 날짜 D 전체를 재현할 만큼 장기 archive 적재량이 아직 부족하다.
- live observability 는 아직 JSONL sidecar 수준이며 대시보드 집계, 알람, broker execution reject telemetry 분리는 남아 있다.
- stale/halt/trade-condition hard gate 는 live smoke 기준으로 다시 검증해야 한다.
- `report_builder.py`, `ai_supervisor.py`, `providers/live_market.py` 는 여전히 단일 파일이 커서 변경 범위가 넓다.
- `premkt_historical_replay.py` 는 bulk load/cursor/scoring 분리 후에도 아직 큰 orchestration 파일이다. 후속 정리는 export/report aggregation, diagnostics writer, CLI-facing runner facade 순서로 작게 나눈다.
- Streamlit UI cleanup v1 은 구조 분리와 첫 화면 정보 구조 보존이 목표이며, 디자인 polish 와 비즈니스 로직 변경은 후속 작업으로 둔다.
- 큰 service 파일 cleanup 은 `report_builder.py` 를 1순위로 두고 facade/API 를 유지한 채 payload loading, markdown export, snapshot HTML renderer, HTML formatting helper 순서로 나눈다.
- KIS mock broker execution 은 `trade-plan` 기반 반자동 검증 범위만 지원하고 auto loop, reconciliation, recovery runbook 이 없다.
- full tape/websocket 기반 실시간 엔진이 아니며 기본 구조는 계속 `replay/mock-first` 성격이 강하다.
- 기존 `momentum_only` 버킷은 pure momentum 이 아니라 watchlist universe 와 watchlist metadata 를 유지한 watchlist-aware momentum 이었다. 현재 scanner input universe 자체가 watchlist/live pipeline 에 묶여 있으므로 이 이름만으로 predictor/watchlist/momentum alpha 를 분리했다고 해석하면 안 된다.
- 현재 단계에서 가능한 비교는 동일 scanned activity universe 안에서 metadata 를 제거하는 `watchlist_blind_momentum` 방식의 within-scan ablation 이며, 진짜 `pure_momentum` 은 independent universe/replay provider 가 분리된 뒤에만 도입한다.
- Step 4/5 리포트는 존재하지만 Step 0 coverage 와 shadow/out-of-sample 검증 전에는 live 판단 근거가 될 수 없다.
- predictor effect disabled 또는 Step 0 coverage 60% 미만 run 은 predictor edge 판단 근거로 쓰지 않는다.
- PremktPredictor 학습 준비 1일차 산출물은 성능 판별이 아니라 누수 없는 학습 데이터셋 생성 기반이다. cutoff 이후 minute bar 가 없으면 row 는 유지하고 label 컬럼은 비워 둔다.
- PremktPredictor 학습 준비 3단계는 모델 점수 연결이며, trading 성능 판단은 아직 아님. 모델 점수에 필요한 historical minute feature 가 부족하면 rule score 로 fallback 하고 JSON lineage 에 이유를 남긴다.
- historical replay smoke 또는 calibration 결과는 최종 성능 판단이 아니다. 과거 백테스트가 좋아도 곧바로 실매매 판단이 아니며, 좋은 OOS 결과의 최대 판정은 `promising_needs_shadow` 다.
- `score_lt45` 는 live strategy 가 아니라 2025년 6월 sanity replay 와 2025년 4~5월 backward robustness 에서 손실 감소 가능성을 본 frozen hypothesis 다. Step 0 coverage, 고정 파라미터 OOS, shadow 검증 전에는 실매매 진입 필터로 해석하지 않는다.
- 2026-04-30 이전 historical replay CSV 의 `analysis_label` 은 EXIT row 에서 청산 시점 label 로 기록됐을 수 있다. `WAIT_PULLBACK` 등 entry label attribution 은 새 replay 산출물의 `entry_analysis_label` / `exit_analysis_label` 기준으로 다시 봐야 한다.
- L2 historical depth 가 없으므로 order book 시뮬레이터는 만들지 않는다. 현재 현실화는 L1 quote, minute volume, halt/resume 상태를 이용한 보수적 proxy 로만 해석한다.

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

## 다음 우선순위

- `BACKTEST_ROADMAP_KO.md` Step -1 성능평가 배선 검증은 완료됐다.
- PremktPredictor 학습 준비 4단계는 point-in-time historical replay runner 구현으로 시작했다. 핵심 원칙은 과거 날짜 D의 판단에 D 이후 데이터와 cutoff 이후 feature 를 쓰지 않는 것이다.
- 즉시 순서는 `진단 CSV가 포함된 1개월 baseline 재생성 -> label/quick-stop 손실 원인 분해 -> entry filter ablation -> stop/exit 구조 ablation -> 고정 파라미터 3개월 이상 OOS` 다.
- Windows historical replay 는 기존 손실 attribution 산출물을 재사용하지 말고 `k1=0,k2=0` baseline 과 label ablation 을 2026-05-01 이후 코드로 다시 생성한다.
- `k2` size amplification 과 winner add/scaling-in 은 entry/stop 구조가 음수 expectancy 를 벗어난 뒤에만 켠다. 현재는 손실을 키우는지 줄이는지 평가할 KPI 배선부터 먼저 만든다.
- 3개월 기준은 실제 시간을 기다리는 운영이 아니라 과거 데이터 재생 기준이며, 개발 루프는 2일 smoke -> 5-10일 sanity -> 1개월 calibration -> 3개월 이상 out-of-sample 순서로 진행한다.
- Step 0 coverage 60% gate 와 L1 archive 적재는 병행 과제다. L1 없는 replay 는 계속 smoke/sanity 판정으로만 유지한다.
- Step 6은 완료됐고 multiday 설정은 `AppSettings` 와 `.env.example` 로 승격됐으며 env override 회귀 테스트와 골든 검증을 통과했다.
- Step 7은 완료됐고 KIS live timestamp 정규화와 live JSONL observability 추가 후 전체 `173 passed` 를 확인했다.
- Step 8은 완료됐고 coverage report latest JSON 과 gate 상태 파일이 기준 경로에 고정됐으며 전체 `176 passed` 를 확인했다.
- Step 9는 완료됐고 intraday `time_stop`, multiday `overnight_hold_rejected`/`loser_replacement` 골든 스냅샷을 추가한 뒤 전체 `179 passed` 를 확인했다.
- Step 10은 완료됐고 로컬 품질 게이트 스크립트와 GitHub Actions CI 를 추가한 뒤 전체 `184 passed` 를 확인했다.
- Step 0 보강으로 `capture-kis-l1-window` 반복 archive runner 를 추가했고 관련/전체 테스트 `185 passed` 를 확인했다.
- 이후에는 Step 0 coverage 확보와 shadow/out-of-sample 검증 순으로 다시 돌아간다.
- UI cleanup v1 이후 남은 구조 정리 후보는 `report_builder.py`, `providers/live_market.py`, `ai_supervisor.py`, `paper_reporting.py`, `market_activity.py` 순서다. `ai_supervisor.py` 는 현재 freshness 테스트 실패 원인을 먼저 분리한 뒤 착수한다.
- live readiness Phase 1 은 timestamp/observability 기반은 들어왔지만 live smoke 와 hard gate 검증 전까지는 여전히 문서/검증 단계로 유지한다.
- 골든 스냅샷과 `tests/golden/` 은 의도된 diff 가 아니면 건드리지 않는다.
- 각 Step 완료 시 이 문서를 먼저 갱신하고, 그 다음 진행 기록과 관련 문서를 맞춘다.
