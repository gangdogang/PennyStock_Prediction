# Step Progress

최종 갱신일: 2026-04-21

## 운영 원칙

- 한 번에 하나의 Step 만 진행한다.
- 각 Step 완료 시 `docs/STATUS.md` 를 먼저 갱신한다.
- 설계, 우선순위, 성능평가 기준, 구현 방향이 바뀌면 작업 전에 관련 `.md` 를 먼저 갱신한다.
- 성능평가 산출물은 코드 수정 근거가 되므로, 해석 결과와 다음 작업은 `docs/STATUS.md` 또는 `docs/BACKTEST_ROADMAP_KO.md` 에 남긴 뒤 구현한다.
- 구조와 문서가 어긋나면 구현보다 문서를 먼저 고친다.
- 병렬화 가능한 조사와 구현은 토큰 비용을 신경 쓰지 말고 서브에이전트를 최대한 적극적으로 사용한다.
- 서브에이전트는 읽기 전용 조사, 경로 확인, 테스트 범위 분리, 구현 분리처럼 책임이 명확한 단위로 나눠 쓴다.
- active 경로인 intraday paper engine 과 KIS mock broker execution 경로는 의도 없이 흔들지 않는다.
- `tests/test_regression_golden.py`, `tests/golden/` 은 의도된 diff 가 아니면 변경하지 않는다.

## 진행 현황

| Step | 상태 | 메모 |
| --- | --- | --- |
| -1 | DONE | predictor score/weight lineage, KPI 분모 정의, bucket divergence smoke, run manifest, performance gate 고정. `./scripts/check_quality.sh` 기준 `199 passed` |
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

## 현재 메모

- Step -1 완료 후에는 새 paper export 를 다시 생성해 이전 CSV의 predictor lineage 공백 여부를 재검증한다.
- 다음 구현 우선순위는 Step 0 coverage 60% gate 확보와 archive 적재다.
- 3개월 검증은 실제 시간을 기다리는 방식이 아니라 과거 데이터 재생 기준이다. 구현 루프는 2일 smoke, 5-10일 sanity, 1개월 calibration, 3개월 이상 out-of-sample 순으로 빠르게 반복한다.
- Step 단위 커밋 원칙을 유지하고, 한 커밋에 여러 Step 을 섞지 않는다.

## KIS timestamp timezone 검증 메모

- 결론: ET
- 근거 요약: `historical_l1_quotes` 최신 archive row 기준 `TSLA | quote_at=2026-04-18T08:59:57-04:00 | created_at=2026-04-18T13:37:33.504548+00:00 | abs delta=37.6분` 으로 2시간 이내였고, 13~14시간 오프셋은 관찰되지 않았다. `live_market.py:547-548,665-666` 와 `kis_historical.py:338-339` 가 같은 `dymd/dhms` 해석 경로를 사용하며, `kis_client.py` 는 field 의미를 해석하지 않는 공통 HTTP wrapper 다.
- 남은 위험: 경험적 증거 기반 확정이라 KIS 응답 포맷이 바뀌면 `capture_l1_quotes` 의 120분 drift canary 가 먼저 경고해야 한다.

2026-04-19 리뷰 Finding 전부 close (4 HIGH / 4 MEDIUM / 4 LOW) — pytest 195 passed
