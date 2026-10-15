"""Use verified first-publication metadata; keep NEWSIS visible registration time."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
specs = json.loads((HERE / 'specs.json').read_text(encoding='utf-8'))
receipts = {r['url']: r for r in json.loads((HERE / 'receipts.json').read_text(encoding='utf-8'))}
changes = []
for row in specs['articles']:
    receipt = receipts[row['url']]
    row['retrieved_at'] = receipt['retrieved_at']
    if receipt['body_hash']:
        row['article_text_hash'] = receipt['body_hash']
    if 'newsis.com' not in row['url']:
        pub = next((m.get('content') for m in receipt.get('date_metadata', []) if m.get('property') == 'article:published_time'), None)
        if pub and row['published_at'] != pub:
            changes.append({'url': row['url'], 'before': row['published_at'], 'after': pub, 'reason': 'First-publication metadata provides seconds omitted by visible minute display'})
            row['published_at'] = pub
    if 'digitaltoday.co.kr' in row['url']:
        row['availability'] = 'full_text'
specs['notes']['20261014'] = '일반 웹 검색; 2026-10-13 15:30 초과~10-14 15:30 이하 KST 최초 공개 기준。13건(전날 마감 후 5건). 시황 2건 감성·방향 0. HBF 동일 연설은 중복 클러스터/후속보도 처리. 판촉·행사 소식 낮은 관련성/중요도. 톱스타뉴스는 검색 색인 본문 일부만 확인(partial_text); 디지털투데이는 공개 HTML 본문 전체를 추가 확인. 뉴시스 SEDEX 기사 메타시각 13:50:26은 수정시각이므로 화면 등록시각 12:30 사용. 10-14 15:33 11번가, 16:59 크래프톤, 17:10 SK스퀘어 분석기사는 창 밖으로 제외. 시세 페이지·잔차 파일은 판단 근거로 사용하지 않음. 미검토 모델 출력.'
(HERE / 'specs.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(HERE / 'time_resolution_notes.json').write_text(json.dumps({'metadata_time_refinements': changes, 'manual_override': {'url': 'https://www.newsis.com/view/NISX20261014_0003825533', 'visible_registered_at': '2026-10-14T12:30:00+09:00', 'meta_article_published_time_is_modified_time': '2026-10-14T13:50:26+09:00'}, 'minute_precision': ['https://www.mk.co.kr/news/stock/12174735'], 'body_receipts_caveat': 'Hashes describe currently retrieved text containers, not an archived first-publication version. Inline live widgets may change. HTML receipt fetches used certificate_verification=false because the workspace proxy certificate is not trusted by requests/certifi; article facts and dates were cross-checked using ordinary web results.'}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(changes, ensure_ascii=False, indent=2))
