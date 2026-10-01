import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
HERE=Path(__file__).resolve().parent
path=HERE/'specs_02.json'
specs=json.loads(path.read_text(encoding='utf-8'))
receipts=json.loads((HERE/'receipts_02.json').read_text(encoding='utf-8'))
for spec,receipt in zip(specs['articles'],receipts):
    # NEWSIS published_time metadata sometimes contains the modification time.
    # Retain the visible initial registration timestamp, verified on the page.
    if 'newsis.com' in spec['url']:continue
    for meta in receipt.get('date_metadata',[]):
        if meta.get('property')=='article:published_time':
            spec['published_at']=datetime.fromisoformat(meta['content'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).isoformat()
            break
        if meta.get('property')=='og:regDate':
            spec['published_at']=datetime.strptime(meta['content'],'%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('Asia/Seoul')).isoformat()
            break
specs['notes']['20260909']+=' 뉴시스 HTML 메타의 수정시각 대신 화면에 명시된 최초 등록 10:53:40을 사용.'
specs['notes']['20260915']+=' 뉴시스 HTML 메타의 수정시각 대신 최초 등록 15:43:38을 사용.'
specs['notes']['20260930']+=' 조선비즈 최초 발행 HTML 메타(UTC)를 KST로 변환; 화면 수정시각과 차이가 있으나 모두 개장 전.'
path.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
