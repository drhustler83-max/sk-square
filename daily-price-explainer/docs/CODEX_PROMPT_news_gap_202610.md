# [Codex 작업 의뢰] SK스퀘어 뉴스 검색 — 2026년 10월 7거래일

## 0. 한 줄 요약

`data/news_search_targets_gap_202610.csv`의 **7개 거래일**(2026-10-01~10-13)에 대해
SK스퀘어 관련 뉴스를 검색하고, 기사별 속성을 스키마대로 추출해 기존 2개 jsonl에
append해주십시오. 방법·스키마·판단 규칙은 `docs/CODEX_PROMPT_news_gap_294.md`와
**완전히 동일**합니다.

---

## 1. 배경

`data/news_search_log_codex.jsonl`이 **2026-09-30**에서 멈춰 있습니다(확인 완료).
직전 작업(39일, 2026-08-04~09-30)까지는 공백 없이 채워졌고, 그 이후 10월분이
새로 생긴 갭입니다. 정량 데이터(F1~F5·F7)의 10월 적재는 별도 사유로 대기 중이지만,
뉴스는 거기에 의존하지 않으므로 먼저 진행할 수 있습니다.

---

## 2. 대상

**`data/news_search_targets_gap_202610.csv`** (7행, `date,sampling_role,reason`
— `sampling_role` 공백, `reason`은 전부 `after_log_end`)

```
20261001, 20261002, 20261006, 20261007, 20261008, 20261012, 20261013
```

(10-05 대체공휴일·10-09 한글날은 휴장이라 제외했습니다. 10-14는 작성 시점에
장중이라 이번 범위에서 뺐습니다 — 다음 배치에 포함하면 됩니다.)

이 7일은 검색 로그에 **하나도 없음을 확인**했습니다. 중복 작업 없습니다.

---

## 3. 검색 방법·출력 형식·판단 규칙 — 294일 작업과 동일

- 검색: 일반 웹 검색(구글 등). 네이버 뉴스 검색 API는 쓰지 마십시오.
  대상은 해당 거래일에 공개된 SK스퀘어 또는 보유자산(SK하이닉스, 드림어스컴퍼니,
  인크로스, 나노엔텍, 크래프톤, 넥써쓰, IONQ, SK쉴더스, 티맵모빌리티, 원스토어,
  콘텐츠웨이브, SK플래닛, 11번가 등) 관련 기사입니다.
- 출력 ①: `data/news_property_codex_batch.jsonl`에 append. 스키마
  `schemas/news_property.schema.json`(`news_property.v1`), 필드 정의는
  `docs/CODEX_PROMPT_news_gap_294.md` §4 그대로.
  `extraction.prompt_version`만 **`"news_gap_202610.v1"`**로.
- 출력 ②: `data/news_search_log_codex.jsonl`에 append. **기사가 0건이어도 반드시
  1줄**(`completed_no_news`) 남겨주십시오.
- 판단 규칙은 같은 문서 §6 그대로:
  1. **결과(주가·잔차)를 보고 라벨을 정하지 마십시오.** `sentiment_score`·
     `ex_ante_impact_direction`은 기사 본문만 보고 판단합니다. 이 구간의 시세를
     조회해 방향 판단에 쓰지 마십시오.
  2. 저품질 기사는 정직하게 표기(본문 못 구하면 `metadata_only`, 후속보도면
     `follow_up`·낮은 `novelty_score`, 단순 시황이면 `market_flow`, 관련성 낮으면
     `relevance_score`를 억지로 올리지 않기).
  3. `confidence_score`는 품질이 아니라 추출값에 대한 확신도.
  4. 거래일 귀속: 장중(09:00~15:30) → 당일, 장마감 후·다음날 개장 전·휴장일 →
     다음 거래일. `event_time_bucket`에 원래 공개 시간대를 남길 것.

---

## 4. 진행 방식

1. 7일이라 한 번에 진행 가능합니다(294일 작업 실측 20일당 약 20분 기준 **10분 내외**).
2. 두 jsonl에 들어갈 줄을 그대로 출력해주십시오.
3. 진행상황(`n/7`)과 기존 파일 append 전후 SHA-256을 함께 보고해주십시오.
4. Sean이 결과를 Claude Code에 붙여넣으면 스키마 검증·중복제거·병합을 수행합니다.

---

## 5. 참고 — 아직 남아 있는 다른 뉴스 작업

이 7일과 별개로, 294일 전수 표본 작업의 **잔여 94일**이 남아 있습니다
(`data/news_search_targets_gap_294_remaining.csv` 중 미완료분,
`docs/CODEX_PROMPT_news_gap_294_remaining.md`). 10-01에 떼어둔 30일 배치
(`data/news_search_targets_gap_294_quick30.csv`)는 아직 실행되지 않았습니다.
이번 10월 7일을 먼저 끝낸 뒤 그쪽으로 넘어가면 됩니다.
