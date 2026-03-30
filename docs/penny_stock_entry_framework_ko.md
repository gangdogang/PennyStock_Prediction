# Penny Stock Entry Framework

이 문서는 Penny Stock Radar가 정규장/프리장에서 어떤 근거로 `조건부 진입`, `재료 확인 전`, `눌림/재돌파 대기`, `추격 금지`를 보여줘야 하는지 정리한 내부 기준이다.

## 핵심 원칙

1. `상승률`만으로는 진입 근거가 부족하다.
- FINRA와 SEC는 저가주가 낮은 거래량과 적은 공개 정보 때문에 조작과 급락에 취약하다고 반복해서 경고한다.
- 따라서 급등 자체는 “관심 신호”일 뿐이고, 공개 재료와 유동성 확인이 없으면 진입 근거로 보기 어렵다.

2. `거래대금`과 `스프레드`는 진입 신호라기보다 `실행 가능성 필터`다.
- 거래대금이 약하면 작은 주문에도 가격이 크게 흔들릴 수 있다.
- 스프레드가 넓으면 방향이 맞아도 즉시 손실이 커질 수 있다.
- 그래서 `거래대금 강함 + 스프레드 안정`은 필요조건에 가깝고, 충분조건은 아니다.

3. `공개 재료 맥락`이 없으면 보수적으로 본다.
- SEC는 공개 정보 부족과 거짓 홍보가 microcap 사기의 핵심 위험이라고 본다.
- FINRA도 뉴스 없이 급등했다가 빠르게 무너지는 small-cap 조작 패턴을 경고한다.
- 따라서 프리장 전 watchlist, SEC filing, 뉴스, 검증 가능한 공시 맥락이 없으면 상위 movers여도 `재료 확인 전`으로 남기는 편이 안전하다.

4. `불타기`는 수직 양봉이 아니라 `확인 후 추가`여야 한다.
- 이 부분은 규제기관이 직접 “이렇게 매수하라”고 말한 규칙이 아니라, 위 위험요인에 기반한 트레이딩 휴리스틱이다.
- 프로그램에서는 `조건부 진입`을 “지금 바로 추격”이 아니라 “첫 눌림 후 VWAP/분봉 고점 재회복 또는 프리장 고점 재돌파가 나올 때만”으로 해석한다.

## 라벨 해석

### `조건부 진입`
- 뜻: 모멘텀, 거래대금, 스프레드, 기존 레이더 맥락이 같이 맞는다.
- 해석: 즉시 시장가 추격이 아니라 `첫 눌림 후 재돌파`에서만 접근.
- 빠지면 안 되는 것:
  - 거래대금 강함
  - 스프레드 양호
  - 프리장 전 watchlist 또는 공개 재료 맥락

### `재료 확인 전`
- 뜻: 가격과 유동성은 강하지만, 왜 오르는지 프로그램이 아직 확인하지 못했다.
- 해석: 뉴스/공시/SEC filing 확인 전 신규 진입 보류.
- 전형적 상황:
  - 실시간 top gainer에는 떴지만 watchlist에 없었음
  - 급등폭은 큰데 공개 재료 맥락이 비어 있음

### `눌림/재돌파 대기`
- 뜻: 힘은 보이나 아직 확정적이지 않다.
- 해석: VWAP 회복, 분봉 고점 재돌파, 시초 range 유지 같은 추가 확인이 필요하다.

### `추격 금지`
- 뜻: 스프레드, 거래대금, 과확장, 모멘텀 약화 중 하나 이상이 문제다.
- 해석: 지금 가격대에서 쫓아가면 손익비가 나쁘다고 본다.

## 실전용 해석 메모

- `상승률 모멘텀 유지됨`
  - 단독으로는 부족하다.
  - 공개 재료나 기존 레이더 맥락이 붙어야 한다.

- `실시간 거래대금이 매우 강함`
  - 매우 중요한 필터다.
  - 특히 penny stock에서는 “움직이는지”보다 “체결 가능한지”를 먼저 본다.

- `스프레드가 과도하지 않음`
  - 실제 매매에서는 매우 중요하다.
  - 이 조건이 빠지면 좋은 셋업도 나쁜 체결로 망가질 수 있다.

## 출처와 해석 범위

- `거래대금/낮은 거래량/스프레드/변동성 위험`:
  - FINRA, Low-Priced Stocks Can Spell Big Problems
    - https://www.finra.org/investors/insights/low-priced-stocks-big-problems
  - FINRA, Regulatory Notice 11-15
    - https://www.finra.org/rules-guidance/notices/11-15
- `공개 정보 부족/허위 홍보/펌프앤덤프 위험`:
  - SEC, Microcap Stock: A Guide for Investors
    - https://www.sec.gov/about/reports-publications/investorpubsmicrocapstock
  - FINRA, Low-Priced Stocks Can Spell Big Problems
    - https://www.finra.org/investors/insights/low-priced-stocks-big-problems
  - FINRA, Regulatory Notice 22-25
    - https://www.finra.org/rules-guidance/notices/22-25
- `거래량과 수익률 지속성 관계`:
  - NBER Working Paper 8312, Dynamic Volume-Return Relation of Individual Stocks
    - https://www.nber.org/papers/w8312

주의:
- 위 문헌들은 `무조건 수익나는 매수 규칙`을 주지 않는다.
- 이 프로그램의 `조건부 진입`, `눌림/재돌파 대기`, `불타기 금지/허용` 해석은 위 위험 문헌과 유동성 문헌을 바탕으로 만든 `보수적 트레이딩 휴리스틱`이다.
