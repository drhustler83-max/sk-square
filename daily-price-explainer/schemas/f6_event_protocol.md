# F6 structured event protocol (v1.0.0)

F6는 뉴스 문장의 LLM 해석이 아니라, 공개 시점을 추적할 수 있는 기업 이벤트의 숫자만 저장한다. 저장 단위는 한 이벤트의 한 소스 버전이며 원본 행은 append-only다. JSON 레코드는 [`f6_event.schema.json`](./f6_event.schema.json)을 따른다.

## 식별자와 분류

- `event_id`: 한 소스 버전의 불변 ID다. 정정은 기존 행을 수정하지 않고 새 ID로 추가한다.
- `deal_id`: 동일 경제 사건의 단계들을 묶는다. 예를 들어 한 자사주 취득 건의 `decision`, `execution`, `result`, `completion`, `cancellation`은 같은 값을 쓴다.
- 초기 `event_type`은 `analyst_target_price`, `treasury_share_acquisition`, `large_shareholding`, `block_trade`다. 새 유형은 스키마 버전과 이 문서를 함께 갱신한 뒤 추가한다.
- `correction=true`이면 `supersedes`는 바로 대체하는 이전 `event_id`여야 한다. 원본이면 `correction=false`, `supersedes=null`이다. 정정 전에는 원본, 정정의 `effective_trading_date`부터는 정정본만 보이는 as-of view를 만든다.
- `source_version`에는 원문 개정 번호가 있으면 그것을, 없으면 원문 해시와 파서 버전을 재현 가능하게 기록한다. `retrieved_at`은 수집 시점일 뿐 공개 시점의 대용값이 아니다.

## Point-in-time 규칙

1. `economic_at`은 거래ㆍ결정ㆍ산정이 실제 발생한 때이고 `known_at`은 해당 숫자가 시장에 최초 공개된 때다. 둘이 다르면 그대로 보존한다. 모델 귀속에는 항상 `known_at`만 사용한다.
2. `known_at`과 `retrieved_at`은 명시적 UTC offset이 있는 RFC 3339로 저장한다. 한국 소스는 보통 `+09:00`이다. 공개 시간이 날짜까지만 확인되면 `known_at_precision=date`와 그 날짜의 `23:59:59+09:00`을 저장해 보수적으로 다음 거래일에 귀속한다. 추정한 장중 시각을 넣지 않는다.
3. `effective_trading_date`는 `known_at`을 입력으로 기존 `tools/regime_events.py`의 당일/다음 거래일 규칙과 거래일 달력에서 계산한다. `economic_at`, `metrics[].as_of`, 파일명, 검색일로 계산하면 안 된다.
4. 피처 생성기는 해당 날짜의 as-of view에서 `model_eligible=true AND backfill_only=false`인 행만 읽는다. 미래 정정본이나 미래 결과보고서로 과거 행ㆍrolling windowㆍ스케일러를 다시 쓰지 않는다. 표준화와 결측치 대체도 확장 학습창 안에서만 fit한다.
5. 원문ㆍ숫자ㆍ공개시각 중 하나라도 검증되지 않으면 `model_eligible=false`로 둔다. 나중 문서로 과거 상태를 복원한 감사용 행은 `backfill_only=true`이며 스키마상 자동으로 모델 부적격이다.

### 결과보고서와 5% 보고서의 backfill 금지

- 자사주 취득 결과보고서에 과거 일별 체결 내역이 있어도 그 숫자는 보고서의 `known_at` 전에는 알려진 것으로 취급하지 않는다. 보고서 자체는 `stage=result`로 공개일 이후 사용할 수 있지만, 과거 거래일별 행을 만들면 반드시 `backfill_only=true`, `model_eligible=false`다. 당시에 별도로 공개된 체결 소스가 있을 때만 그 소스의 고유 `known_at`으로 독립 실행 이벤트를 만든다.
- 대량보유상황보고(5%룰)의 취득ㆍ처분일 또는 계약일은 `economic_at`/`metrics[].as_of`다. 지분 변화 피처는 신고서의 실제 `known_at`에 따른 `effective_trading_date`부터만 반영한다. 보고기한 때문에 늦게 알려진 변화를 거래일로 소급하지 않는다.
- 같은 원칙을 사후 확인된 블록딜과 정정공시에도 적용한다. 정정은 정정 공개일 이후에만 원본을 대체한다.

## 숫자 사전

한 이벤트 안에서 `metrics[].name`은 중복하지 않는다. 방향 값은 텍스트 판정이 아니라 숫자 차이로 계산하며, SK스퀘어 관점의 매입ㆍ지분 증가를 양수, 매각ㆍ지분 감소를 음수로 통일한다.

| event_type | 권장 metric name | unit |
|---|---|---|
| `analyst_target_price` | `target_price_krw`, `previous_target_price_krw`, `target_revision_pct`, `target_direction` | `KRW_per_share`, `pct`, `count` |
| `treasury_share_acquisition` | `planned_shares`, `planned_amount_krw`, `executed_shares`, `executed_amount_krw`, `cumulative_executed_shares`, `cumulative_executed_amount_krw`, `completion_ratio` | `shares`, `KRW`, `ratio` |
| `large_shareholding` | `stake_pct`, `previous_stake_pct`, `stake_delta_pp`, `changed_shares` | `pct`, `percentage_point`, `shares` |
| `block_trade` | `block_shares`, `block_amount_krw`, `signed_stake_pct` | `shares`, `KRW`, `pct` |

`target_revision_pct = 100 * (target_price_krw / previous_target_price_krw - 1)`이며 이전 목표가가 확인되지 않으면 생성하지 않는다. `target_direction`은 이 값의 부호 `-1/0/+1`이다. `completion_ratio`는 같은 `deal_id`의 누적 실행수량을 결정공시의 계획수량으로 나눈 값이다. 비율의 분모와 분자는 모두 그 시점 as-of view에서 알려진 값이어야 한다.

## 880거래일 표본의 권장 집계 피처 8개

희소 이벤트에서 자유도를 억제하기 위해 아래 8개를 사전 고정한다. `20d`는 현재 거래일을 포함한 20거래일 창이며, 정정 적용 후 `deal_id`로 중복 제거한다. 시가총액 분모는 각 이벤트가 유효해지기 직전 거래일 종가 기준으로 고정해 당일 종가 누출을 막는다. flow형 피처는 사건이 없으면 0이다.

| feature | 계산 |
|---|---|
| `f6_tp_revision_pct_20d` | 창 안 목표가 수정률의 중앙값 |
| `f6_tp_direction_balance_20d` | 목표가 상향 수에서 하향 수를 뺀 뒤 유효 수정 보고서 수로 나눈 값; 보고서가 없으면 0 |
| `f6_buyback_authorized_value_mcap_20d` | 결정 단계의 계획금액/직전 시가총액을 합산 |
| `f6_buyback_executed_value_mcap_20d` | 공개 시점 기준 실행금액/직전 시가총액을 합산; 사후 일별 복원분 제외 |
| `f6_buyback_completion_ratio_latest` | 진행 중인 `deal_id`별 최신 공개 누적 실행률의 합; 진행 건이 없으면 0 |
| `f6_large_holder_stake_delta_pp_20d` | 5% 보고로 새로 알려진 지분율 증감(%p)의 부호 있는 합 |
| `f6_block_trade_stake_pct_20d` | 블록딜 수량/발행주식수의 SK스퀘어 관점 부호 있는 합 |
| `f6_distinct_material_deal_count_20d` | 창 안에서 처음 유효해진 고유 `deal_id` 수; 정정ㆍ후속 문서 수를 중복 계수하지 않음 |

윈도 길이, eligibility, 결측 처리, 부호 규칙은 방향 라벨을 본 뒤 바꾸지 않는다. 다른 창은 이 8개 기본 사양을 동결한 뒤 별도 민감도 분석으로만 평가한다.
