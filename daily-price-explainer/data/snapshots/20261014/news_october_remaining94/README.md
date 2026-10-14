# 2026-10-14 뉴스 검색 인계

① 2026년 10월 7일을 먼저 완료·출력한 뒤, ② 잔여 94일을 20/20/20/20/14일로 완료했다. 총 기사 129줄과 검색 로그 101줄을 원본 JSONL에 append했다. 두 파일의 기존 바이트는 보존되었으며 각 파일을 임시 파일과 os.replace로 원자적으로 저장했다.

현재 data/news_property_codex_batch.jsonl 및 data/news_search_log_codex.jsonl에 이미 반영되어 있다. OUTPUT_FOR_CLAUDE.md는 이번 추가분의 복사·검증용 자료이며 기존 파일 전체를 포함하지 않는다. Claude는 현재 파일을 검증·병합 대상으로 사용하면 된다. 새 기사 extraction.review_status는 전부 unreviewed이다.

| 배치 | 대상 | 완료일 수 | 기사 줄 | 로그 줄 | prompt_version |
|---|---|---:|---:|---:|---|
| 00 | 10월 | 7 | 12 | 7 | news_gap_202610.v1 |
| 01 | 잔여 94일 | 20 | 26 | 20 | news_gap_294_remaining.v1 |
| 02 | 잔여 94일 | 20 | 27 | 20 | news_gap_294_remaining.v1 |
| 03 | 잔여 94일 | 20 | 25 | 20 | news_gap_294_remaining.v1 |
| 04 | 잔여 94일 | 20 | 22 | 20 | news_gap_294_remaining.v1 |
| 05 | 잔여 94일 | 14 | 17 | 14 | news_gap_294_remaining.v1 |

| 파일 | 작업 전 줄 수 | 최종 줄 수 | 작업 전 SHA-256 | 최종 SHA-256 |
|---|---:|---:|---|---|
| news_property_codex_batch.jsonl | 1257 | 1386 | 7190f18fc9d8933daa60f989fc0678177e73213e81b2d714322a469aa7b56a5d | 34a244e25c294fbd707ffea5bec9119617bf059e10a1cb6c4cbb59892ea680f3 |
| news_search_log_codex.jsonl | 1088 | 1189 | 54495023a1b9dd016a5a69d9b38d869e517eaa444e6a57d00c1bcb4695920625 | 68d3f04c78a258ff46ecf4aad4ca46d8dd8e763d91c678deb33e6d03fd4aa1b4 |

각 batchXX_manifest.json에 바로 직전·직후 해시와 추가분 해시를 저장했다. batchXX_articles.jsonl 및 batchXX_logs.jsonl은 실제 append한 줄 그대로다. BATCHXX_FOR_CLAUDE.md는 배치별, OUTPUT_FOR_CLAUDE.md는 전체 복사본이다. remaining_pending.csv는 헤더만 남아 미완료 날짜가 없다.

검증 결과는 validation_summary.json 및 quality_coverage_audit.json에 있다. 129개 신규 기사 모두 news_property.v1 스키마·시각 형식·ID 해시 검증을 통과했다. 신규 URL/ID 중복과 거래일 귀속 오류는 없고, 101개 대상 날짜에 로그가 정확히 한 줄씩 있다. 전체 검색 로그 1189개 날짜는 20211130~20261013 사이 연속이며 중복이 없다. 거래일 달력에는 상장 첫날 20211129가 추가로 있으나 기존 검색 로그와 이번 두 대상에 포함되지 않아 해당 날짜는 별도로 표시했다.

검색은 일반 웹 검색으로 진행했다. search_queries_00_02.json, search_queries_03.json, search_queries_04.json, search_queries_05.json에 실제 쿼리를 보존했다. 네이버 뉴스 검색 API는 사용하지 않았다. 원본 시세·잔차를 조회하지 않았으며 거래일 달력은 날짜 열만 사용했다. 기사에 포함된 시세는 방향 판단에 사용하지 않았고 market_flow 22건은 sentiment_score와 ex_ante_impact_direction이 모두 0이다.

본문 확보 수준은 full_text 16건, partial_text 112건, metadata_only 1건이다. 검색에서 읽을 수 있는 본문 일부로만 추출한 기사는 partial_text로 두었으며, 방송 질문 안내만 있는 20231016 기사는 metadata_only·event_stage unknown·방향 0으로 기록했다. 본문 해시 31건은 현재 접근 가능한 선택 본문 컨테이너의 공백 정규화 UTF-8 SHA-256이고 최초 발행본의 보증은 아니다. 안정적인 본문 선택이나 원문 접근이 불가능한 경우 해시는 null이다. receipts_XX.json에 HTTP 상태·제목·발행 메타·해시 방식만 보존했으며 원문 전체는 저장하지 않았다.

시각 판단에서는 최초 공개와 수정 시각을 구분했다. 뉴시스의 published_time 메타가 수정 시각을 가리키는 사례는 화면 최초 등록을 우선했다. 딜사이트의 20230926 기사는 본문에 명시된 유료 서비스 최초 공개를 사용했다. 조선비즈/조선일보의 검색 화면과 직접 발행 메타 차이, 한국정경신문의 GMT 표기 불일치, 20250116 머니투데이 기사의 늦은 수정 메타는 로그 notes에 표시했다. 개별 시각 판단은 해당 배치 specs_XX.json과 receipts_XX.json을 함께 확인하면 된다.

MOU·검토·개발 샘플·양산 계획·실적 추정·기사 의견을 확정된 합병 완료·투자 집행·수주 매출·발표 실적과 구분했다. 후속 보도에는 낮은 신규성을 사용하고 동일 사건은 가능한 범위에서 duplicate_cluster_id로 연결했다. 정성 점수는 Claude의 의미 검증 대상이다.

다음 배턴: Sean/Claude가 새 기사 129건과 검색 로그 101건의 의미·출처·시각 예외를 검증하고 기존 병합 절차를 진행한다. 두 대상의 미완료 날짜는 없다. 이번 작업은 뉴스 JSONL과 이 인계 디렉터리에 한정했고 커밋·푸시는 하지 않았다. 상위 Git 저장소의 기존 수기 공매도 입력 파일 변경은 별도 작업이며 건드리지 않았다.
