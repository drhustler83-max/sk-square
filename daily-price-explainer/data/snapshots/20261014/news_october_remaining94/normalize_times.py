import json,sys
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
HERE=Path(__file__).resolve().parent
batch=int(sys.argv[1]);path=HERE/f'specs_{batch:02}.json'
specs=json.loads(path.read_text(encoding='utf-8'))
receipts=json.loads((HERE/f'receipts_{batch:02}.json').read_text(encoding='utf-8'))
for receipt in receipts:
    if 'news.nate.com' in receipt['url'] and '\ufffd' in (receipt.get('page_title') or ''):
        response=requests.get(receipt['url'],timeout=15,verify=False,headers={'User-Agent':'Mozilla/5.0'})
        soup=BeautifulSoup(response.content,'html.parser')
        receipt['page_title']=soup.title.get_text(' ',strip=True) if soup.title else None
        receipt['detected_encoding']=soup.original_encoding
protected=specs.get('keep_visible_times',[])
for spec,receipt in zip(specs['articles'],receipts):
    if spec['url'] in protected:continue
    for meta in receipt.get('date_metadata',[]):
        if meta.get('property',meta.get('name'))=='article:published_time':
            try:spec['published_at']=datetime.fromisoformat(meta['content'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).isoformat()
            except ValueError:continue
            break
path.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(HERE/f'receipts_{batch:02}.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
