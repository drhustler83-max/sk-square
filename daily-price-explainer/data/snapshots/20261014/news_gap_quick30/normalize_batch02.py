import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
HERE=Path(__file__).resolve().parent
path=HERE/'specs_02.json';specs=json.loads(path.read_text(encoding='utf-8'))
receipt_path=HERE/'receipts_02.json';receipts=json.loads(receipt_path.read_text(encoding='utf-8'))
for spec,receipt in zip(specs['articles'],receipts):
    if 'news.nate.com' in spec['url']:
        response=requests.get(spec['url'],timeout=15,verify=False,headers={'User-Agent':'Mozilla/5.0'})
        soup=BeautifulSoup(response.content,'html.parser')
        receipt['detected_encoding']=soup.original_encoding
        receipt['page_title']=soup.title.get_text(' ',strip=True) if soup.title else None
        receipt['encoding_note']='Byte parser charset detection; article facts came from readable general web search snippet.'
    # The page explicitly distinguishes the original NEWSIS registration from modification.
    if 'NISX20260331_0003570651' in spec['url']:continue
    for meta in receipt.get('date_metadata',[]):
        if meta.get('property')=='article:published_time':
            spec['published_at']=datetime.fromisoformat(meta['content'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).isoformat();break
path.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
receipt_path.write_text(json.dumps(receipts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
