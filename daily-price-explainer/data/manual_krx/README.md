# 수기 시장 데이터 원본

브라우저 또는 HTS에서 내려받은 원본 파일을 그대로 보존한다. `factor_log.csv`는
직접 편집하지 않고 검증된 임포터로 갱신한다.

## ⚠ 규칙

- **다운로드한 원본을 그대로 둘 것.** 컬럼명과 파일 형식을 바꾸지 않는다.
- 기존 `factor_log.csv`는 사본 검증·해시 대조 뒤 원자적으로 교체한다.

## 파일 이름 규칙

| 데이터 | 파일명 | KRX 메뉴 | 받을 때 |
|---|---|---|---|
| **수급(F3)** | `krx_investor_402340.xlsx` | HTS 투자자별 순매수 수량 | 2022-11-09~2026-09-30, `tools/import_investor_quantity.py` |
| **공매도(F4)** | `krx_short_402340.xlsx` | 공매도 거래량·비율과 대차잔고 | 추후 검토 |
| **신용·대차** | `krx_credit_lending_402340.xlsx` | 신용·대차 자료 | 추후 검토 |

- 추가 원본은 별도 파일로 보존하고 날짜 범위와 단위를 먼저 확인한다.

## 매핑 목표 (factor_log 컬럼)

- 수급 → `foreign_net`, `institution_net`, `individual_net`(수량 주), `foreign_own_pct`(네이버 trend)
- 공매도 → `shorting_balance`(잔고수량), `shorting_balance_ratio`(잔고율%),
  `shorting_volume_ratio`(당일 공매도 비중%), `shorting_balance_change`(잔고 전일대비, 임포터가 계산)

현재 확보된 `krx_investor_402340.xlsx`의 투자자별 수량을
`tools/import_investor_quantity.py`로 적재한다. 기존 거래대금(원) 값은
수량(주)으로 교체하며 원 환산은 하지 않는다. 실시간 수집은 네이버 trend를
사용한다. HTS와 네이버가 겹치는 날짜에도 값 차이가 있으므로 출처 전환은
`data/snapshots/20260930/f3_quantity_import_manifest.json`에 기록했다.
이 파일의 2022년 말 37거래일은 투자자별 값이 비어 있고,
2021-11-29~2022-11-08은 수량 원천이 없어 결측으로 둔다.
