# [Codex 작업 의뢰] SK스퀘어 뉴스 검색 294일 전수 표본 — 남은 124일 마무리

## 0. 한 줄 요약

`docs/CODEX_PROMPT_news_gap_294.md`(294일 전수 표본 작업)이 **170/294일 완료,
124일 남은 상태**입니다. `data/news_search_targets_gap_294_remaining.csv`의
남은 **124개 거래일**을 마저 검색·추출해 같은 2개 jsonl에 append해주십시오.
방법·스키마·판단 규칙은 294일 작업과 **완전히 동일**하니 그 문서를 그대로
따르면 됩니다. 이번 요청은 "남은 분량 마무리"일 뿐 새 프로토콜이 아닙니다.

---

## 1. 배경

294일 전수 표본 작업(2026-09-17 의뢰)의 목적은 Phase 1 뉴스 실험의 매칭
case-control 표본(고잔차 440일 + 대조 440일)이 가진 선택편의를 없애는
것이었습니다. 지금까지 170일이 완료됐고(`news_search_log_codex.jsonl`에
`trading_date`로 확인), 아래처럼 **124일이 남아 있습니다**:

| 연도 | 남은 일수 |
|---|---|
| 2023 | 59 |
| 2024 | 24 |
| 2025 | 36 |
| 2026 | 5 |

(참고: 2021·2022년분과 2026-08-04~09-30 구간은 이미 완료됐습니다. 2026년의
남은 5일은 2026-09-17 이후 생긴 것이 아니라 2023~2025년과 같은 "표본에서
빠진 과거 산발일"(`not_in_matched_sample`)입니다 — 별도 조치 불필요.)

---

## 2. 대상

**`data/news_search_targets_gap_294_remaining.csv`** (124행, `date,sampling_role,reason`
— `sampling_role`은 전부 `unused_low`, `reason`은 전부 `not_in_matched_sample`)

기존 294일 목록(`data/news_search_targets_gap.csv`)에서 이미 완료된 170일을
뺀 것입니다. `news_search_log_codex.jsonl`에 이 124일은 **하나도 없음을
확인**했습니다. 중복 작업 걱정 없습니다.

---

## 3. 검색 방법·출력 형식·판단 규칙 — 294일 작업과 완전히 동일

- 검색: 일반 웹 검색(구글 등). 네이버 뉴스 검색 API는 쓰지 마십시오(2021~2023년
  구조적 조회 불가 — `docs/CODEX_PROMPT_news_gap_294.md` §3 참고).
- 출력 ①: `data/news_property_codex_batch.jsonl`에 append, 스키마
  `schemas/news_property.schema.json`(`news_property.v1`). 필드 정의는
  `docs/CODEX_PROMPT_news_gap_294.md` §4 그대로. `extraction.prompt_version`만
  `"news_gap_294_remaining.v1"`로 바꿔주십시오.
- 출력 ②: `data/news_search_log_codex.jsonl`에 append, 기사가 없어도 1줄
  남길 것(`completed_no_news`). 형식은 같은 문서 §5.
- 판단 규칙(결과로 라벨 정하지 않기, 저품질 기사 정직하게 표기, confidence_score는
  품질이 아닌 확신도, 거래일 귀속 규칙)은 같은 문서 §6 그대로 적용해주십시오.

---

## 4. 진행 방식

1. 124일이니 20일씩 7배치 정도로 나눠 진행해주십시오(294일 때 실측 20일당
   약 20분 — 총 2시간 내외 예상). 한 번에 다 하셔도 무방합니다.
2. 배치마다 두 jsonl에 들어갈 줄을 그대로 출력해주십시오.
3. 진행상황(`n/124`)을 함께 보고해주십시오.
4. Sean이 결과를 Claude Code에 붙여넣으면 스키마 검증·중복제거·병합을 수행합니다.

---

## 5. 완료 후

124일이 끝나면 294일 전수 표본 작업이 완결되고, 거기에 오늘 끝낸 39일
(2026-08-04~09-30)까지 더하면 **뉴스 검색이 상장 이후 전 구간 전수**가
됩니다. 그러면 Claude Code가 `experiment_residual_news.py`·
`experiment_f6_f7_news.py`를 전수 표본 기준으로 재실행해 Phase 1 뉴스 결론을
확정 또는 수정할지 판단합니다.

질문 있으시면 말씀해주십시오.
