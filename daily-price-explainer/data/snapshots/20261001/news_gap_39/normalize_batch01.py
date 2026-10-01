import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
HERE=Path(__file__).resolve().parent
specs=json.loads((HERE/'specs_01.json').read_text(encoding='utf-8'))
receipts={r['url']:r for r in json.loads((HERE/'receipts_01.json').read_text(encoding='utf-8'))}
for article in specs['articles']:
    for meta in receipts[article['url']].get('date_metadata',[]):
        if meta.get('property')=='article:published_time' and meta.get('content'):
            # Newsis 11 Aug meta reflects revised publication; visible initial registration retained.
            if 'NISX20260811_' not in article['url']:
                article['published_at']=datetime.fromisoformat(meta['content'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).isoformat()
            break
    if '/6248191' in article['url']:
        article['evidence_facts']=article['evidence_facts'][:1]
specs['notes']['20260804']='일반 웹 날짜별·보유자산 보완검색. HBF 기사는 HTML 최초 발행시각 09:16:05 확인. 가격 결과로 방향 판단하지 않음.'
specs['notes']['20260812']+=' 뉴시스 마감 기사는 페이지 최초 등록 15:40:17을 사용; HTML 메타에는 수정본 시각 16:56:23이 표기됨. 둘 다 8/12 귀속.'
(HERE/'specs_01.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

