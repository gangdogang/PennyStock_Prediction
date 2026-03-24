# Penny Stock Radar 판단 가이드

이 문서는 대시보드와 CLI 결과를 볼 때 `무엇을 먼저 봐야 하는지`, `각 숫자를 어떻게 해석해야 하는지`를 한국어로 정리한 참고 문서입니다.

## 1. 큰 흐름

이 프로그램은 하루를 4단계로 나눠서 봅니다.

1. `전체 후보군`
   - 미국 저가주 전체에서 기본 조건에 맞는 종목을 추림
2. `프리장 전 분석`
   - 오늘 움직일 가능성이 있는 후보를 watchlist로 압축
3. `프리장 중 분석`
   - 실제로 움직인 종목이 진짜인지, 가짜 급등인지 판별
4. `정규장 분석`
   - 프리장 강세가 continuation인지, dump인지 최종 판별
5. `실시간 모드`
   - 지금도 실제로 거래가 살아있는지, 호가가 너무 얇지 않은지 확인

## 2. 전체 후보군에서 볼 것

핵심 컬럼:

- `price`
- `market_cap`
- `float_shares`
- `passed_filters`
- `filter_reasons`

좋게 보는 경우:

- 가격이 너무 높지 않음
- 시가총액과 유통주식수가 작음
- `passed_filters = yes`

나쁘게 보는 경우:

- `market_cap_too_large`
- `float_too_large`
- `rights_security`
- 가격이 범위를 벗어남

한 줄 해석:

`전체 후보군은 매매 판단용이 아니라, 볼 가치가 있는 종목만 남기는 1차 필터다.`

## 3. 프리장 전 분석에서 볼 것

핵심 컬럼:

- `total_score`
- `catalyst_score`
- `technical_score`
- `sympathy_score`
- `social_score`
- `reasons`

좋게 보는 경우:

- `total_score`가 높음
- `low_float`
- `volatility_contraction`
- 공시/재료 관련 이유가 있음
- 같은 섹터/테마 동조화가 있음

주의할 점:

- 점수가 높아도 실제 수급이 안 붙으면 의미 없음
- 이 단계는 `예측`이 아니라 `후보 압축` 단계

한 줄 해석:

`프리장 전 분석은 오늘 볼 만한 종목을 줄이는 단계다.`

## 4. 프리장 중 분석에서 볼 것

핵심 컬럼:

- `quality_score`
- `premarket_rvol`
- `dollar_volume`
- `tps`
- `spread_pct`
- `round_level_break`
- `tape_anomaly`
- `reasons`

가장 먼저 볼 3개:

- `quality_score`
- `dollar_volume`
- `spread_pct`

좋게 보는 경우:

- `quality_score` 높음
- `dollar_volume` 충분함
- `premarket_rvol` 높음
- `spread_pct` 너무 크지 않음
- `close_above_pm_vwap`
- `tape_anomaly`와 거래 증가가 같이 나타남

나쁘게 보는 경우:

- `spread_pct`가 너무 큼
- 거래량은 있는데 거래대금이 약함
- 숫자는 튀는데 실제 체결이 빈약함

한 줄 해석:

`프리장 중 분석은 “지금 뜨는 종목이 진짜인지”를 거르는 단계다.`

## 5. 정규장 분석에서 볼 것

핵심 컬럼:

- `decision`
- `anchored_vwap`
- `extension_z`
- `mfe_pct`
- `mae_pct`
- `reasons`

`decision` 해석:

- `ENTER`
  - 현재 구조상 진입 가능성이 가장 좋게 본 케이스
- `WATCH`
  - 아직 확신이 부족하니 지켜보는 단계
- `AVOID`
  - 구조가 안 좋거나 실패 확률이 높다고 본 케이스

같이 보면 좋은 것:

- `anchored_vwap` 위에 있는지
- `extension_z`가 너무 과열이 아닌지
- `mfe_pct`와 `mae_pct`의 균형

한 줄 해석:

`정규장 분석은 프리장 강세가 이어질지, 설거지인지 최종 판단하는 단계다.`

## 6. 실시간 모드에서 볼 것

핵심 컬럼:

- `source`
- `market_phase`
- `trade_price`
- `bid_price`
- `ask_price`
- `spread_pct`
- `updated_at`

가장 먼저 볼 3개:

- `trade_price`
- `spread_pct`
- `updated_at`

좋게 보는 경우:

- `source = alpaca` 또는 `polygon`
- `trade_price`가 계속 갱신됨
- `bid_price`, `ask_price`가 둘 다 있음
- `spread_pct`가 너무 크지 않음
- `updated_at` 시간이 계속 바뀜

나쁘게 보는 경우:

- 값이 전부 `-`
- `updated_at`이 안 바뀜
- `spread_pct`가 지나치게 큼
- 호가 한쪽이 비어 있음

중요:

- 이 모드는 `웹소켓 틱 스트리밍`이 아니라 `주기적 polling`
- 즉 완전한 tick-by-tick 실시간은 아님

한 줄 해석:

`실시간 모드는 “이 종목이 지금도 살아 있는지” 확인하는 단계다.`

## 7. API가 없을 때와 있을 때 차이

API가 없을 때:

- 실시간 모드는 동작하지 않음
- 마지막으로 저장된 분석 결과를 보여줌
- 최신화하려면 `run_full_pipeline.sh`를 다시 실행해야 함

API가 있을 때:

- 실시간 모드 탭에서 최신 quote/trade/snapshot을 주기적으로 조회
- 대시보드에서 `실시간 자동 새로고침` 가능

## 8. 추천 사용 순서

장 시작 전:

1. `launch_dashboard.command` 실행
2. `프리장 전 분석`에서 상위 후보 확인

프리장 중:

1. `프리장 중 분석` 확인
2. `quality_score`, `dollar_volume`, `spread_pct` 우선 확인

정규장:

1. `정규장 분석` 확인
2. `decision`과 `anchored_vwap` 관련 이유 확인

API가 있다면:

1. `실시간 자동 새로고침` 켜기
2. `실시간 모드` 탭에서 `updated_at`과 `spread_pct` 확인

## 9. 기억할 핵심 한 줄

- `전체 후보군` = 1차 필터
- `프리장 전 분석` = 후보 압축
- `프리장 중 분석` = 진짜/가짜 구분
- `정규장 분석` = continuation/dump 판정
- `실시간 모드` = 지금도 실제로 거래 가능한 상태인지 확인
