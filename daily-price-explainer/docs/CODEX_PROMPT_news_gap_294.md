# [Codex 작업 의뢰] SK스퀘어 뉴스 검색 294일 — 전수 표본 완성

## 0. 한 줄 요약

`data/news_search_targets_gap.csv` 의 **294개 거래일**에 대해 SK스퀘어 관련 뉴스를 검색하고,
기사별 속성을 스키마대로 추출해 2개 jsonl 에 append 해주십시오. 20일씩 배치로 진행하고
배치마다 결과를 붙여넣어 주시면 Claude Code 가 검증·병합합니다.

---

## 1. 배경

SK스퀘어(KOSPI 402340) IR 팀이 **DPE(Daily Price Explainer)** 라는 주가 변동 설명 챗봇의
설명 엔진을 만들고 있습니다. 핵심 타깃은 `divergence` — SK스퀘어 주가 수익률에서
NAV 기반 기대수익률을 뺀 잔차입니다.

Phase 1 에서 이미 880거래일치 뉴스를 검색·추출하셨고(감사합니다), 그 데이터로 뉴스가
잔차를 설명하는지 검정했습니다. **결과는 기각**이었습니다 — 뉴스 단독 ROC-AUC 0.506,
부호정확도 0.507. 기업뉴스/매크로뉴스로 쪼개서 재검정해도 마찬가지였습니다.

### 그런데 결정적 결함이 하나 남아 있습니다

880일은 **매칭 case-control 표본**이었습니다. 고잔차일 440일 + 국면·변동성이 비슷한
대조일 440일을 짝지은 것으로, 비용 때문에 전수 검색을 못 해서 택한 설계입니다.
나머지 **294일(전 거래일의 25%)은 아예 검색된 적이 없습니다.**

이 294일을 채우면 **1,174거래일 전수 표본**이 됩니다. 그러면:

- 매칭 설계가 불필요해지고 **선택편의가 원천적으로 사라집니다**
- 표본이 880 → 1,174 일로 늘어 검정력이 올라갑니다
- "뉴스에 신호가 없다" 와 "표본이 부족했다" 를 비로소 구분할 수 있습니다

즉 이 작업은 단순 데이터 보충이 아니라 **Phase 1 결론을 확정짓는 마지막 검증**입니다.

---

## 2. 대상

**`data/news_search_targets_gap.csv`** (294행)

| 컬럼 | 설명 |
|---|---|
| `date` | 거래일 `YYYYMMDD` |
| `sampling_role` | 기존 표본에서의 역할 (`unused_low` 263일 / 빈값 31일) |
| `reason` | `not_in_matched_sample` 263일 / `after_log_end` 31일 |

연도별 분포: 2021년 1일 · 2022년 74일 · 2023년 123일 · 2024년 24일 · 2025년 36일 · 2026년 36일

기존에 검색 완료된 880일은 이 파일에 없습니다. **중복 작업이 없습니다.**

---

## 3. 검색 방법 — 네이버 API 쓰지 마십시오

네이버 뉴스 검색 API 는 `start` 파라미터가 최대 1000 이라 "SK스퀘어" 같은 일반 쿼리로는
최근 1~2주치밖에 못 봅니다. 2021~2023년 날짜는 **구조적으로 조회가 불가능**합니다.
Phase 1 에서 이미 확인된 사항이며, **일반 웹 검색(구글 등)** 으로 진행하셨습니다.
같은 방식으로 부탁드립니다.

검색 대상은 해당 거래일에 공개된 **SK스퀘어 또는 그 보유자산 관련 기사**입니다.
보유자산: SK하이닉스, 드림어스컴퍼니, 인크로스, 나노엔텍, 크래프톤, 넥써쓰, IONQ,
SK쉴더스, 티맵모빌리티, 원스토어, 콘텐츠웨이브, SK플래닛, 11번가 등.

---

## 4. 출력 ① — 기사 속성 (`news_property_codex_batch.jsonl` 에 append)

기사 1건당 JSON 1줄. 스키마는 `schemas/news_property.schema.json` (`news_property.v1`).

### 필수 필드

```jsonc
{
  "schema_version": "news_property.v1",
  "article_id": "<canonical URL + 본문해시 기반 SHA-256>",
  "trading_date": "YYYYMMDD",          // 귀속 거래일 (아래 §6 규칙)
  "url": "https://...",
  "publisher": "연합뉴스",
  "published_at": "2023-05-11T08:12:00+09:00",   // 없으면 null
  "event_time_bucket": "pre_open",     // pre_open | intraday | post_close | non_trading_day | unknown
  "retrieved_at": "2026-09-17T14:00:00+09:00",
  "article_text_hash": "<본문 해시>",  // 없으면 null
  "availability": "full_text",         // full_text | partial_text | metadata_only | unavailable
  "language": "ko",
  "title": "…",

  "primary_subject": "sk_square",
  // sk_square | sk_hynix | portfolio_company | shareholder_policy | governance
  // | semiconductor_sector | macro_market | regulation | other

  "entities": ["SK스퀘어", "SK하이닉스"],

  "relevance_score": 0.0~1.0,
  "event_type": "capital_return",
  // earnings | capital_return | value_up | portfolio_acquisition | portfolio_disposal
  // | ipo | financing | governance | regulation | semiconductor_cycle | analyst_opinion
  // | market_flow | litigation | management_change | other

  "event_stage": "announcement",
  // rumor | expectation | proposal | announcement | approval | execution
  // | completion | follow_up | unknown

  "novelty_score": 0.0~1.0,
  "sentiment_score": -1.0~1.0,
  "surprise_direction": "positive",    // negative | neutral | positive | unknown
  "surprise_magnitude": 0.0~1.0,
  "materiality_score": 0.0~1.0,
  "uncertainty_score": 0.0~1.0,
  "forward_looking": true,
  "ex_ante_impact_direction": 1,       // -1 | 0 | 1 (정수)
  "impact_horizon": "one_to_three_days",
  // intraday | one_to_three_days | one_to_four_weeks | structural | unknown

  "mechanisms": ["nav_discount", "capital_allocation"],
  // nav | nav_discount | cash_flow | capital_allocation | governance
  // | supply_demand | sector_beta | macro | valuation_multiple | unclear

  "confidence_score": 0.0~1.0,

  "numeric_claims": [
    {"metric": "자사주 매입금액", "value": 1000, "unit": "억원",
     "period": null, "comparison": null}
  ],

  "evidence_facts": ["…", "…"],        // 최대 5개, 짧은 요약. 원문 장문 복사 금지
  "duplicate_cluster_id": null,        // 같은 사건 중복보도면 동일 ID 부여

  "extraction": {
    "extractor": "codex",
    "model": "<사용 모델명>",
    "prompt_version": "news_gap_294.v1",
    "extracted_at": "2026-09-17T14:00:00+09:00",
    "review_status": "unreviewed"
  }
}
```

`additionalProperties: false` 입니다. **위에 없는 키를 추가하면 검증에서 탈락합니다.**

---

## 5. 출력 ② — 검색 로그 (`news_search_log_codex.jsonl` 에 append)

**기사가 하나도 없어도 반드시 1줄 남겨주십시오.** 이게 없으면 "검색했는데 없었다" 와
"검색을 안 했다" 를 구분할 수 없어 표본이 다시 오염됩니다.

```jsonc
{"trading_date": "20230511", "sampling_role": "unused_low",
 "status": "completed_with_articles", "articles_found": 3, "notes": ""}

{"trading_date": "20230512", "sampling_role": "unused_low",
 "status": "completed_no_news", "articles_found": 0, "notes": "관련 기사 없음"}
```

`status` 는 `completed_with_articles` 또는 `completed_no_news` 둘 중 하나입니다.

---

## 6. 판단 규칙 — Phase 1 실패에서 배운 것

### ① 결과를 보고 라벨을 정하지 마십시오 (가장 중요)

스키마 설명에 명시돼 있습니다 — *"article-only properties extracted **without price,
divergence, high_residual, direction, or importance labels**"*.

그날 주가가 올랐는지 내렸는지, 잔차가 컸는지 **절대 참고하지 마십시오.**
`sentiment_score`·`ex_ante_impact_direction` 은 **기사 본문만 보고** 판단해야 합니다.
결과를 보고 맞추면 검정 자체가 무의미해집니다.

### ② 저품질 기사를 정직하게 표기하십시오

Phase 1 에서 **전체 기사의 58.4% 가 시황 자동생성·후속보도·본문없음**이었고,
이들을 제거하자 신호가 실무 문턱 아래로 떨어졌습니다. 즉 **가짜 신호의 주범**이었습니다.

- 본문을 못 구했으면 `availability: "metadata_only"` — 제목만 보고 추측해 채우지 마십시오
- 이미 보도된 사안의 후속이면 `event_stage: "follow_up"`, `novelty_score` 낮게
- 단순 시황·수급 기사면 `event_type: "market_flow"`
- 관련성이 낮으면 `relevance_score` 를 낮게. **억지로 올리지 마십시오**

빈약한 날은 빈약하게 기록되는 게 맞습니다.

### ③ `confidence_score` 는 "확신도" 이지 "품질" 이 아닙니다

Phase 1 에서 이 필드가 `relevance`·`materiality` 와 상관 0.75 이상이 나와
사실상 품질 대리변수로 변질됐습니다. **추출한 속성값 자체에 대한 확신**만 표현해주십시오.
관련성 낮은 기사라도 속성을 확실히 읽어냈으면 `confidence_score` 는 높을 수 있습니다.

### ④ 거래일 귀속 (`trading_date`)

- 장중(09:00~15:30) 공개 → 당일
- 장마감 후·다음날 개장 전·휴장일 공개 → **다음 거래일**
- `event_time_bucket` 에 원래 공개 시간대를 별도로 남겨주십시오

---

## 7. 진행 방식

1. **20일씩 배치**로 진행해주십시오 (Phase 1 실측: 20일당 약 20분)
2. 배치마다 두 jsonl 에 들어갈 줄들을 그대로 출력해주십시오
3. 진행상황(`n/294`)과 소요시간을 함께 보고해주십시오
4. Sean 이 그 결과를 Claude Code 에 붙여넣으면 스키마 검증·중복제거·병합을 수행합니다
5. 이슈가 있으면(검색 불가, 애매한 귀속 등) `notes` 에 남기거나 별도로 알려주십시오

**294일 전체는 약 5시간 분량입니다.** 나눠서 진행하셔도 됩니다.

---

## 8. 완료 후

Claude Code 가 다음을 수행합니다.

- 전수 1,174일 표본으로 뉴스 재검정 (매칭 설계 폐기)
- `experiment_residual_news.py` · `experiment_f6_f7_news.py` 재실행
- Phase 1 보고서의 뉴스 결론 확정 또는 수정

질문 있으시면 말씀해주십시오.
