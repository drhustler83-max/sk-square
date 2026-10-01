# 수기 시장 데이터 원본

브라우저 또는 HTS에서 내려받은 원본 파일을 그대로 보존한다. `factor_log.csv`는
직접 편집하지 않고 검증된 임포터로 갱신한다.

## ⚠ 규칙

- **다운로드한 원본을 그대로 둘 것.** 컬럼명과 파일 형식을 바꾸지 않는다.
- 기존 `factor_log.csv`는 사본 검증·해시 대조 뒤 원자적으로 교체한다.

## 파일 이름 규칙

| 데이터 | 파일명 | KRX 메뉴 | 받을 때 |
|---|---|---|---|
| **수급(F3)** | `krx_investor_402340.xlsx` | HTS 투자자별 순매수 수량(통합) | 2022-11-09~2026-09-30, 비교·보존용 |
| **공매도(F4)** | `krx_short_402340.xlsx` | 공매도 거래량·비율과 대차잔고 | 추후 검토 |
| **신용·대차** | `krx_credit_lending_402340.xlsx` | 신용·대차 자료 | 추후 검토 |

- 추가 원본은 별도 파일로 보존하고 날짜 범위와 단위를 먼저 확인한다.

## 매핑 목표 (factor_log 컬럼)

- 수급 → `foreign_net`, `institution_net`, `individual_net`(수량 주), `foreign_own_pct`(네이버 trend)
- 공매도 → `shorting_balance`(잔고수량), `shorting_balance_ratio`(잔고율%),
  `shorting_volume_ratio`(당일 공매도 비중%), `shorting_balance_change`(잔고 전일대비, 임포터가 계산)

2026-10-01 Sean 확정: **KRX 수량·외국인+기타외국인**으로 통일한다.
HTS 원본은 통합(KRX+NXT) 기준이며 ‘외국인’ 열은 기타외국인을 제외한다.
따라서 이 원본으로 현재 F3를 다시 덮어쓰면 안 된다.
`tools/import_investor_quantity.py --write`는 중단하도록 변경했다.

현재 과거·실시간 원천은 네이버 PC
`/api/domestic/detail/402340/trend?tradeType=KRX`이며,
과거 적재는 `tools/import_naver_krx_quantity.py`를 사용한다.
상장~2026-09-30 1,183일 원문·해시·비교·변경 매니페스트는
`data/snapshots/20261001/f3_source_scope/`에 있다. 가격 열은 가져오지 않는다.
2021-11-29~2022-11-08 233일은 실제 수량 원천으로 복구했다.
**2022-11-09~12-29 37일은 Sean의 ‘넘어가자’ 지시로 결측 유지**한다.
원문에 수량이 있어도 이 구간의 CSV F3 값은 채우지 않는다.
원 환산·종가 곱셈은 하지 않는다.

자세한 검증과 범위 한계는 `docs/F3_SOURCE_SCOPE_20261001.md`를 참조한다.
