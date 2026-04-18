# 2엔진 전환 설계

이 문서는 기존 단일 paper trading 흐름을 `intraday` 와 `multiday` 두 엔진으로 분리하기 위한 기준 문서다.

## 목표

- `engine_intraday`: 오늘 강한 종목을 스캔하고 당일 위주로 대응
- `engine_multiday`: 프리장 전에 후보를 찍고 며칠 보유하며 승자에만 불타기
- watchlist, live scan, report, automation은 공통 기반을 최대한 공유

## 왜 분리하는가

- intraday와 multiday는 진입 이유가 다르다.
- intraday와 multiday는 청산 이유가 다르다.
- intraday와 multiday는 리스크 관리 기준이 다르다.
- 하나의 엔진에 섞으면 성과 해석이 흐려진다.

## 엔진 A: intraday

역할:

- live mover 스캔
- premarket / regular intraday 진입
- 빠른 손절, 부분익절, 짧은 보유
- halt, stale quote, spread, trap score 가드레일 강화

핵심 규칙:

- 신규 진입은 premarket / regular 둘 다 허용하되 시간 창을 명시적으로 분리
- 당일 장 종료 전 청산을 기본값으로 둔다
- 불타기는 정규장 승자에만 제한적으로 허용
- sizing은 `per-trade risk + stop distance + spread/slippage` 기준으로 계산

필요 구현:

1. `PaperTradingEngine` 에서 intraday 전용 클래스를 분리
2. 정규장 신규 진입 규칙을 명시적으로 추가
3. risk-based sizing 으로 실제 주문 수량 계산 통일
4. trade condition / abnormal print 필터 반영

## 엔진 B: multiday

역할:

- 프리장 전 후보 3~5개 선정
- 소액 분산 진입
- 며칠 보유
- 살아남는 승자에만 추가 매수
- 매일 새 강세주가 나타나면 자본을 교체

핵심 규칙:

- 초기 진입은 아주 작게
- 패자 물타기 금지
- `평단 위` 에서만 add 허용
- add는 `거래대금 유지 + 구조 유지 + 재료 훼손 없음` 조건에서만 허용
- overnight 리스크는 별도 점수로 평가

필요 상태:

- `starter`
- `add_1`
- `add_2`
- `trail`
- `overnight_hold`
- `exit`

필요 구현:

1. `MultidayTradingEngine` 신규 생성
2. `overnight_hold_score`, `day2_survival_score` 계산 로직 추가
3. 보유일수, add 단계, 교체 사유를 position 필드나 notes로 추적
4. `session_closed` 자동청산 대신 보유 유지 여부 판단 도입

## 공통 계층

공통으로 재사용할 것:

- universe / watchlist 생성
- market activity scan
- trade plan blockers
- live provider
- report export
- automation shell

공통으로 분리할 것:

- sizing helper
- slippage helper
- market status / stale data / quote validation helper
- position lifecycle state helper

## 추천 폴더 방향

기존 파일을 즉시 이동하지 말고, 아래 순서로 간다.

1. 기존 active 파일은 유지
2. 새 엔진 파일을 병렬 추가
3. 기존 intraday 동작을 `engine_intraday` 로 고정
4. multiday 엔진을 신규 추가
5. 충분히 분리된 뒤에만 리팩터링

추천 목표 구조:

```text
src/penny_stock_radar/engines/
  intraday/
  multiday/
  shared/
```

초기 단계에서는 아래처럼 시작해도 충분하다.

```text
src/penny_stock_radar/services/intraday_engine.py
src/penny_stock_radar/services/multiday_engine.py
src/penny_stock_radar/services/engine_shared.py
```

## 리포트 방향

intraday KPI:

- win rate
- average hold minutes
- avg win / avg loss
- slippage impact
- stop execution quality

multiday KPI:

- top winner contribution
- one-big-winner dependency
- avg hold days
- add-on contribution
- overnight gap damage
- portfolio heat

## 바로 하지 말 것

- 기존 파일을 대량 rename 하면서 한 번에 다 고치기
- intraday 규칙과 multiday 규칙을 한 클래스에 억지로 합치기
- 승률만 보고 전략을 판단하기

## 현재 active 기준

- active executor는 여전히 intraday 성격이 강하다
- multiday는 새 엔진으로 별도 구현해야 한다
- 기존 intraday 핵심 복사본은 `archive/engine_split_2026-04-18/` 에 보관되어 있다
