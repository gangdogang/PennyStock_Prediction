# Multiday Plan Tracker

## 목표

`MultidayTradingEngine`에 `starter 진입 -> overnight hold -> winner add -> loser replacement -> day2/day3 exit` 흐름을 단계적으로 구현하고 추적한다.

## 현재 상태

- `MultidayTradingEngine` 초기 뼈대는 존재한다.
- 이번 작업에서 1차 핵심 규칙 4개 작업 단위를 모두 코드와 테스트에 반영했다.
- 새 컨텍스트에서는 `docs/multiday_plan/` 아래에서 이 문서를 기준 문서로 사용한다.

## 작업 단위 표

| ID | 작업명 | 범위 | 선행조건 | 상태 | 진척도 | 완료기준 |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | starter + overnight | 최초 진입, overnight hold score, session close hold/reject | multiday 엔진 뼈대 | DONE | 35% | starter 진입, overnight 유지/거부, 테스트 통과 |
| 02 | winner add | 승자 불타기, add stage, add guardrail | 01 완료 | DONE | 20% | 승자 add 허용/차단, 상태 갱신, 테스트 통과 |
| 03 | loser replacement | 약한 포지션 교체, strict upgrade, churn 방지 | 01 완료 | DONE | 20% | replacement 발생/거부, churn 제한, 테스트 통과 |
| 04 | day2/day3 exit | day2 gap fail, continuation failure, day3 exhaustion, trail/hard stop | 01 완료 | DONE | 25% | day2/day3 exit reason 기록, 테스트 통과 |

## 각 작업 상태

- `01`: DONE
- `02`: DONE
- `03`: DONE
- `04`: DONE

## 전체 진척도

- `100%`

## 최근 완료 항목

- multiday starter 진입 규칙 추가
- overnight hold 유지/거부 분기 추가
- winner add와 add stage 상태 전이 추가
- loser replacement와 strict upgrade 규칙 추가
- day2/day3 exit reason 표준화 및 테스트 추가

## 지난 이슈

- `REFACTOR_FOLLOWUP_KO.md` Step A 에서 multiday fill-model 호출부 회귀를 해소했고, 이 이슈는 현재 종료 상태다.

## archive 메모

- 이 tracker 는 현재 이슈 추적보다는 참고용 기록에 가까우므로, Step G 이후 `archive/` 이동 후보로 검토한다.

## 다음 작업

- multiday KPI를 report/dashboard/cohort 요약에 노출
- trade plan에서 multiday 컨텍스트를 별도 버킷으로 다루기
- multiday 전용 sizing/stop 파라미터를 settings로 승격할지 검토

## blocker 메모

- 현재 multiday 메타데이터는 기존 `PaperPosition` / `PaperOrder` / `PaperTradingRun` 필드를 재사용한다.
- 세부 전략 파라미터는 아직 코드 상수 위주라, 추후 설정화가 필요할 수 있다.
- 단계별 프롬프트 문서는 제거했고, 진행 상태와 다음 작업은 이 문서 하나로 추적한다.
