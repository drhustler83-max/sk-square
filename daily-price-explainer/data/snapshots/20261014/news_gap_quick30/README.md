# 뉴스 quick30 전달 기록

2026-10-14 Codex. 지정된 quick30 대상 30일 완료. 일반 웹 검색만 사용했으며 네이버 뉴스 API는 사용하지 않았다.
1차 배치: 20일, 기사 25줄, 로그 20줄. 2차 배치: 10일, 기사 14줄, 로그 10줄.
합계 기사 39줄과 로그 30줄을 기존 두 JSONL에 원자적으로 append했다. 마지막 append는 14:43:47 KST.
모든 기사 속성은 unreviewed, extraction.prompt_version=news_gap_294_remaining.v1이다.
Sean/Claude Code 후속 검증용 로컬 결과이며 커밋·푸시는 하지 않았다.

복사용 출력: OUTPUT_FOR_CLAUDE.md, BATCH01_FOR_CLAUDE.md, BATCH02_FOR_CLAUDE.md.
같은 작업폴더의 JSONL에는 이미 추가됐으므로 출력물을 다시 append하지 않는다.
나머지 94일은 로그에 추가하지 않았다. 원래 대상 CSV는 그대로 두고 다음 작업용 목록을 remaining_after_quick30.csv에 저장했다.

검증 및 근거:

- validation_summary.json: 새 39건의 JSON Schema 검증, 30일별 로그 정확히 1줄, 기사 수 일치,
  신규 URL/ID 중복 없음, 발행시각별 귀속 및 기존 바이트 보존을 검증했다.
- initial_manifest.json 및 batch01_manifest.json/batch02_manifest.json: 전후 SHA-256, 원본 바이트 길이와 추가분 해시.
- search_queries.json: 실제 일반 웹 검색 쿼리. 날짜별 SK스퀘어 검색과 적응형 보유자산 보완검색 결과를 수록했다.
  기사 전수 망라를 뜻하지 않으며 광고, 커뮤니티 투자전략, 검색 목록만 있는 페이지 등은 제외했다.
- trading_calendar_dates.json: factor_log.csv에서 date 열만 읽어 만든 1183일 달력.
  산발적인 30일 목록을 달력으로 쓰지 않았다. 정량 가격·잔차 값을 기사 라벨 판단에 사용하지 않았다.
- receipts_01.json/receipts_02.json: HTTP 상태, 발행 메타, 가능한 본문 컨테이너 해시. 기사 원문 장문은 저장하지 않았다.
  본문 컨테이너 해시는 공백 정규화 텍스트의 UTF-8 SHA-256이다. 명확히 확보하지 못하면 null로 두었다.
- 기사 ID는 SHA256(URL + '|' + (본문 해시 또는 빈 문자열))이다.
- 본문을 전체 확인한 7건은 full_text, 일부 확인한 32건은 partial_text다.
  단순 market_flow 17건은 sentiment와 ex ante 방향을 모두 0으로 두었다.

귀속/품질 특이 사항:

- 스마트투데이 3월 27일 자사주 기사는 검색·발행 메타 09:51:12와 이동된 페이지 화면 18:51이 달라 제외했다.
  거래일이 달라질 수 있어 임의로 시각을 선택하지 않았다. excluded_sources_01.json과 해당일 로그에 기록했다.
- 뉴시스 2026년 3월 31일 KB증권 기사 메타는 수정시각이어서 화면 최초 등록 09:02:32를 사용했다.
- MTN 2026년 7월 9일 인터뷰는 기사 내 청약대금 달러 표기끼리 일치하지 않아 해당 숫자를 추출하지 않았다.
  ADR의 재평가 기대와 국내 전환·수급 위험을 함께 읽고 사전 방향을 중립으로 두었다.
- 검사장비 공급계약은 공급자 계약액을 SK하이닉스 매출 효과로 해석하지 않았다.
- 로보뉴스·가격 시황·행사 르포는 자동생성 또는 낮은 신규성/관련성/중요도 성격을 명시했다.
- 일부 장마감 후 기사는 다음 거래일이 이번 quick30 대상 밖이므로 제외했다.

재검증: `.venv\Scripts\python.exe -X utf8 data/snapshots/20261014/news_gap_quick30/validate_delivery.py`.
build_batch.py는 기존 배치 파일/로그 중복을 거부한다. fetch/normalize 스크립트는 작업 당시 보조도구이므로 재실행할 필요가 없다.
정량 CSV·NAV·스케줄러·실험은 이번 작업에서 변경하거나 실행하지 않았다.
