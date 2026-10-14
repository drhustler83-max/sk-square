"""Fetch public article containers; keep hashes and metadata, never full copyrighted text."""
import concurrent.futures, hashlib, json, re
from pathlib import Path
import requests
import urllib3
from bs4 import BeautifulSoup
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HERE=Path(__file__).resolve().parent
SELECTORS={'mt.co.kr':'[itemprop="articleBody"]','newsis.com':'[itemprop="articleBody"]','v.daum.net':'#harmonyContainer .article_view'}
def fetch(spec):
    url=spec['url']
    r={'url':url,'body_hash':None,'body_chars':0}
    try:
        response=requests.get(url,timeout=20,verify=False,headers={'User-Agent':'Mozilla/5.0'})
        r['http_status']=response.status_code
        if response.status_code!=200:return r
        response.encoding='utf-8'
        soup=BeautifulSoup(response.text,'html.parser')
        r['page_title']=soup.title.get_text(' ',strip=True) if soup.title else None
        r['date_metadata']=[{k:m.get(k) for k in ('property','name','content') if m.get(k)} for m in soup.select('meta') if 'date' in (m.get('property','')+' '+m.get('name','')).lower() or 'time' in (m.get('property','')+' '+m.get('name','')).lower()]
        for domain,selector in SELECTORS.items():
            if domain in url:
                body=soup.select_one(selector)
                if body:
                    for bad in body.select('script,style,iframe'):bad.decompose()
                    value=' '.join(body.get_text(' ',strip=True).split())
                    if len(value)>150 and '\ufffd' not in value:
                        r.update(body_hash=hashlib.sha256(value.encode('utf-8')).hexdigest(),body_chars=len(value),selector=selector,hash_recipe='SHA256(UTF8(collapsed-whitespace selected article container text))')
        return r
    except Exception as error:r['error']=type(error).__name__;return r
if __name__=='__main__':
    import sys
    batch=int(sys.argv[1]); path=HERE/f'specs_{batch:02}.json'
    specs=json.loads(path.read_text(encoding='utf-8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        receipts=list(pool.map(fetch,specs['articles']))
    for spec,receipt in zip(specs['articles'],receipts):
        if receipt['body_hash']:
            spec['article_text_hash']=receipt['body_hash']
    path.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (HERE/f'receipts_{batch:02}.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for receipt in receipts:
        print(json.dumps(receipt,ensure_ascii=False))
