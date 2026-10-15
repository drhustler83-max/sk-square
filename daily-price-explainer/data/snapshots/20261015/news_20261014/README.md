2026-10-14 뉴스 검색·추출 — Codex, 2026-10-15

대상 `news_search_targets_gap_20261014.csv`의 하루를 완료했다. 기사 13줄과 검색 로그 1줄을 누적 JSONL에 이미 append했다. prompt_version은 `news_gap_20261014.v1`, 신규 review_status는 모두 `unreviewed`다. Claude Code의 검증·검토·커밋을 위해 `OUTPUT_FOR_CLAUDE.md`에 추가된 JSONL 원문과 전후 해시를 제공한다. 같은 줄을 다시 append하지 않는다.

귀속 창은 2026-10-13 15:30 초과부터 10-14 15:30 이하까지 KST다. 전날 마감 후 5건, 당일 개장 전 2건, 장중 6건이다. 기사 내용으로만 속성을 판단했다. 시황 2건은 sentiment_score와 ex_ante_impact_direction이 모두 0이다. HBF 동일 기조연설의 두 기사는 같은 클러스터이며 후속 기사의 신규성을 낮췄다. 판촉·행사 예고는 관련성·중요도를 낮게 기록했다. 일반 웹 검색에서 따라 나온 시세 스니펫은 방향 판단에 사용하지 않았고 시세 API·잔차 파일을 조회하지 않았다.

제외한 주요 후보:

- 파이낸셜뉴스 2026-10-14 17:10 SK스퀘어 BNK 분석: `https://www.fnnews.com/news/202610141708238087` — 마감 후, 10-15 귀속.
- 뉴스핌 2026-10-14 16:59 크래프톤 게임 AI B2B: `https://www.newspim.com/news/view/20261014000938` — 10-15 귀속.
- 더퍼스트미디어 2026-10-14 15:33 11번가 행사: `https://www.thefirstmedia.net/news/articleView.html?idxno=210807` — 10-15 귀속. 동일 행사 당일 아침 뉴시스 원문은 이번 창에 포함.
- 게임뷰 2026-10-14 18:11 넥써쓰 실적 전망: `https://www.gamevu.co.kr/news/articleView.html?idxno=61435` — 10-15 귀속.
- 아이보스 인크로스 리포트: `https://www.i-boss.co.kr/ab-3208-1830` — 10-13 날짜만 확인되며 마감 전후를 확정할 시각을 확보하지 못해 제외.
- 같은 사건의 전재·포토 기사, 크립토 Waves 동음이의어, 기존 기사에 현재 날짜 시세 위젯만 붙은 검색 결과는 별도 신규 사건으로 세지 않았다. 일반 웹 검색으로 확인한 대표 원문을 수록했으며 모든 매체의 전수 포착을 보장하지 않는다.

시각과 본문 가용성:

- 뉴시스 SEDEX 기사 `NISX20261014_0003825533`의 메타 `article:published_time=13:50:26`은 화면상 수정 시각이다. 화면의 최초 등록 `12:30:00`을 유지했다. 다른 매체는 확인 가능한 최초 발행 메타데이터로 초 단위를 보완했다.
- 디지털투데이는 웹 열기 실패 후 공개 HTML로 본문 전체를 추가 확인했다. 톱스타뉴스는 검색 색인에서 읽은 본문 일부 범위로만 추출하여 `partial_text`다. 본문 해시는 확보·확인 가능한 컨테이너 7건에만 기록했고 나머지는 null이다. null 해시는 본문 미열람을 뜻하지 않는다.
- 해시는 현재 수집 시점의 본문 컨테이너를 대상으로 하며 최초 발행본의 보관 해시는 아니다. 일부 본문 내 실시간 위젯이 수집본에 반영될 수 있다. 저작권 본문 전체는 파일로 보관하지 않았다.
- 로컬 requests의 프록시 인증서 검증 오류 때문에 공개 HTML 영수증 수집은 certificate_verification=false로 실행했다. 이 제한을 receipts.json 및 time_resolution_notes.json에 명시했다. 기사 내용과 최초 시각은 일반 웹 결과와 대조했다.

검증과 변경 범위:

- initial_manifest.json으로 원본 두 파일의 SHA-256·바이트 수·행 수를 고정했다. 별도 validated_copy 사본에서 전체 JSON 파싱, 신규 행 스키마·중복·귀속을 확인한 뒤 os.replace로 각 파일을 원자적으로 교체했다. 변경 전 바이트와 일치하는지 저장 직전 다시 확인했다.
- append_manifest.json은 원본 접두 바이트 길이, 전후 해시, 추가분 해시, 행 수를 기록한다. validate_after.py로 기존 접두 바이트 보존 및 추가분과 누적 파일 접미부의 일치를 확인했다.
- 신규 13행은 Draft 2020-12 + FormatChecker를 통과했다. 기존 데이터의 스키마 위반 14건은 변경하지 않았다: primary_subject=market_flow 8건, mechanisms에 regulation 2건, extraction.extractor=claude 4건. 정확한 행·URL은 validation_summary.json에 있다. 신규 배치 통과와 전체 레거시 스키마 상태를 구분해 보고한다.
- 기사 ID 중복 0, 신규 canonical URL 중복 0. 로그 1,190일은 기존 저장 거래일 캘린더(2021-11-30~2026-10-13)와 이번 10-14 타깃의 합집합에 정확히 일치한다. 상장 당일 2021-11-29는 기존 설계대로 제외했다.
- 원본 CSV, NAV, F3/F4, 스케줄러, 잔차 생성기는 변경·실행하지 않았다. 커밋·푸시는 이번 의뢰 문서 §5의 Claude Code 검증·병합 단계에 남긴다.

재검증: `.venv\Scripts\python.exe -X utf8 data/snapshots/20261015/news_20261014/validate_after.py`

다음 배턴: Claude Code가 OUTPUT_FOR_CLAUDE.md와 append_manifest.json, validation_summary.json을 확인하고 신규 13건을 검토한다. 기존 스키마 위반 14건은 별도 정리 범위로 판단한다. 10-14 마감 후 후보는 10-15 뉴스 작업에서 재검토한다.
