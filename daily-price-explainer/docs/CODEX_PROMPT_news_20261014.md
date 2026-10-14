# [Codex 작업 의뢰] SK스퀘어 뉴스 — 10월 7일 + 전수표본 잔여 94일

## 0. 한 줄 요약

두 묶음을 순서대로 처리해주십시오. 방법·스키마·판단 규칙은
`docs/CODEX_PROMPT_news_gap_294.md`와 **완전히 동일**합니다.

| 순서 | 대상 | 파일 | 일수 | prompt_version |
|---|---|---|---|---|
| ① 먼저 | 2026년 10월 갭 | `data/news_search_targets_gap_202610.csv` | 7 | `news_gap_202610.v1` |
| ② 다음 | 294일 전수표본 잔여 | `data/news_search_targets_gap_294_remaining94.csv` | 94 | `news_gap_294_remaining.v1` |

①을 먼저 끝내고 결과를 출력한 뒤 ②로 넘어가주십시오. ①이 끝나면 검색 로그가
상장 이후 2026-10-13까지 **공백 없이 연속**이 됩니다.

---

## 1. 현재 상태

- `data/news_search_log_codex.jsonl`: **1,088행**, 최대 `trading_date` = **20260930**
- `data/news_property_codex_batch.jsonl`: **1,257행**
- 직전 quick30 작업분(30일·39기사)은 검증 후 커밋 완료(`5dc540a`)
- 294일 전수표본 작업: **200/294 완료**, 잔여 94일(2023년 59 / 2024년 24 / 2025년 11)

두 대상 파일의 날짜는 검색 로그에 **하나도 없음을 확인**했습니다. 중복 작업 없습니다.

---

## 2. ① 2026년 10월 7거래일

```
20261001, 20261002, 20261006, 20261007, 20261008, 20261012, 20261013
```

10-05(대체공휴일)·10-09(한글날)은 휴장이라 제외했습니다.
**10-14는 제외**했습니다 — 목록 작성 시점에 장중이었습니다. 다음 배치에 넣으면 됩니다.

상세: `docs/CODEX_PROMPT_news_gap_202610.md`

---

## 3. ② 전수표본 잔여 94일

`data/news_search_targets_gap_294_remaining94.csv` (94행, 전부
`sampling_role=unused_low`, `reason=not_in_matched_sample`).
quick30 작업에서 Codex가 직접 산출한 `remaining_after_quick30.csv`와 같은 내용입니다.

94일이라 20일씩 5배치 정도로 나눠주십시오(실측 20일당 약 20분 → 총 1.5~2시간).
시간이 부족하면 완료한 날짜까지만 보고하고, **미완료 날짜는 로그에 쓰지 말고
남겨주십시오** — 그래야 다음에 이어서 할 수 있습니다.

상세: `docs/CODEX_PROMPT_news_gap_294_remaining.md`
(그 문서는 124일 기준으로 쓰여 있는데, quick30 30일이 끝나 지금은 94일입니다.)

---

## 4. 출력 — 두 묶음 공통

- 기사: `data/news_property_codex_batch.jsonl`에 append.
  스키마 `schemas/news_property.schema.json`(`news_property.v1`),
  필드 정의는 `docs/CODEX_PROMPT_news_gap_294.md` §4 그대로.
  `extraction.prompt_version`만 위 표의 값으로.
- 검색 로그: `data/news_search_log_codex.jsonl`에 append.
  **기사가 0건이어도 반드시 1줄**(`completed_no_news`).
- 배치마다 두 jsonl에 들어갈 줄을 그대로 출력하고, append 전후 SHA-256과
  진행상황(`n/7`, `n/94`)을 함께 보고해주십시오.

---

## 5. 판단 규칙 — 반드시 유지

1. **결과(주가·잔차)를 보고 라벨을 정하지 마십시오.** `sentiment_score`·
   `ex_ante_impact_direction`은 기사 본문만 보고 판단합니다. 해당 구간의 시세를
   조회해 방향 판단 근거로 쓰지 마십시오.
2. 저품질 기사는 정직하게 표기 — 본문 못 구하면 `metadata_only`, 후속보도면
   `follow_up`·낮은 `novelty_score`, 단순 시황이면 `market_flow`, 관련성 낮으면
   `relevance_score`를 억지로 올리지 않기.
3. `confidence_score`는 품질이 아니라 추출값 자체에 대한 확신도.
4. 거래일 귀속: 장중(09:00~15:30) → 당일, 장마감 후·다음날 개장 전·휴장일 →
   다음 거래일. `event_time_bucket`에 원래 공개 시간대를 남길 것.
5. 검색은 일반 웹 검색(구글 등). 네이버 뉴스 검색 API는 쓰지 마십시오.

---

## 6. 완료 후

②까지 끝나면 294일 전수표본이 완결되고, ①과 합쳐 **상장 이후 전 구간 전수**가
됩니다. 그러면 Claude Code가 `experiment_residual_news.py`·
`experiment_f6_f7_news.py`를 전수 표본으로 재실행해 Phase 1 뉴스 결론을 확정
또는 수정할지 판단합니다.

Sean이 결과를 Claude Code에 붙여넣으면 스키마·중복·귀속·해시 검증 후 병합합니다.
