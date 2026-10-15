# [Codex 작업 의뢰] SK스퀘어 뉴스 — 2026-10-14 하루치

## 0. 한 줄 요약

`data/news_search_targets_gap_20261014.csv`의 **1거래일(2026-10-14)**에 대해
SK스퀘어 관련 뉴스를 검색하고, 기사별 속성을 스키마대로 추출해 기존 2개 jsonl에
append해주십시오. 방법·스키마·판단 규칙은 `docs/CODEX_PROMPT_news_gap_294.md`와
**완전히 동일**합니다. `extraction.prompt_version`만 `"news_gap_20261014.v1"`로.

---

## 1. 현재 상태

- `data/news_search_log_codex.jsonl`: **1,189행**, 최대 `trading_date` = **20261013**
- `data/news_property_codex_batch.jsonl`: **1,386행**
- 직전 작업(10월 7일 + 전수표본 잔여 94일)은 검증 후 커밋 완료(`857daa7`)
- 20261014는 검색 로그에 **없음을 확인**했습니다. 중복 작업 없습니다.

이 하루를 채우면 검색 로그가 **상장 다음날(2021-11-30)부터 2026-10-14까지
공백 없이 연속**이 됩니다. (상장 당일 20211129는 애초에 어느 타깃 목록에도
없었던 날이라 그대로 비어 있습니다.)

---

## 2. 대상

**2026-10-14 (수)** 1일.

귀속 창은 **2026-10-13 15:30 ~ 2026-10-14 15:30 KST**입니다. 10-14 장마감 후
기사는 다음 거래일(10-15) 귀속이므로 **이번 대상이 아닙니다.**

---

## 3. 검색 방법·출력

- 검색: 일반 웹 검색(구글 등). **네이버 뉴스 검색 API는 쓰지 마십시오.**
  대상은 SK스퀘어 또는 보유자산(SK하이닉스, 드림어스컴퍼니, 인크로스, 나노엔텍,
  크래프톤, 넥써쓰, IONQ, SK쉴더스, 티맵모빌리티, 원스토어, 콘텐츠웨이브,
  SK플래닛, 11번가 등) 관련 기사입니다.
- 출력 ①: `data/news_property_codex_batch.jsonl`에 append.
  스키마 `schemas/news_property.schema.json`(`news_property.v1`), 필드 정의는
  `docs/CODEX_PROMPT_news_gap_294.md` §4 그대로.
- 출력 ②: `data/news_search_log_codex.jsonl`에 append.
  **기사가 0건이어도 반드시 1줄**(`completed_no_news`).
- append 전후 SHA-256을 함께 보고해주십시오.

---

## 4. 판단 규칙 — 반드시 유지

1. **결과(주가·잔차)를 보고 라벨을 정하지 마십시오.** `sentiment_score`·
   `ex_ante_impact_direction`은 기사 본문만 보고 판단합니다. 이 날짜의 시세를
   조회해 방향 판단 근거로 쓰지 마십시오.
2. 저품질 기사는 정직하게 표기 — 본문 못 구하면 `metadata_only`, 후속보도면
   `follow_up`·낮은 `novelty_score`, 단순 시황이면 `market_flow`, 관련성 낮으면
   `relevance_score`를 억지로 올리지 않기.
3. `confidence_score`는 품질이 아니라 추출값 자체에 대한 확신도.
4. 거래일 귀속: 장중(09:00~15:30) → 당일, 장마감 후·다음날 개장 전·휴장일 →
   다음 거래일. `event_time_bucket`에 원래 공개 시간대를 남길 것.

직전 배치에서 `market_flow` 22건이 전부 `sentiment_score=0`·
`ex_ante_impact_direction=0`으로 들어온 것을 Claude Code가 확인했습니다.
같은 기준을 유지해주십시오.

---

## 5. 완료 후

Sean이 결과를 Claude Code에 붙여넣으면 해시 체인·스키마·중복·귀속·건수 정합을
검증한 뒤 병합·커밋합니다.
