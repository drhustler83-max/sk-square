import json
from pathlib import Path
import requests
import urllib3
from bs4 import BeautifulSoup
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HERE = Path(__file__).resolve().parent
for receipt in json.loads((HERE / 'receipts.json').read_text(encoding='utf-8')):
    print(json.dumps({k: receipt.get(k) for k in ['url', 'http_status', 'body_hash', 'body_chars', 'date_metadata', 'container_candidates']}, ensure_ascii=False))
url = 'https://www.digitaltoday.co.kr/news/articleView.html?idxno=706904'
soup = BeautifulSoup(requests.get(url, timeout=25, verify=False).content, 'html.parser')
body = soup.select_one('#article-view-content-div')
if body:
    for bad in body.select('script,style,iframe'):
        bad.decompose()
    print('DIGITALTODAY_BODY_READ_ONLY: ' + body.get_text(' ', strip=True))
print('EXISTING_EVENT_CLUSTERS:')
for line in (HERE.parents[3] / 'data/news_property_codex_batch.jsonl').read_text(encoding='utf-8').splitlines():
    row = json.loads(line)
    if row.get('trading_date') in ['20261012', '20261013'] and any(term in (row.get('title') or '') for term in ['엘리시티', 'OCP', '솔리다임', 'CXMT', '창신']):
        print(json.dumps({key: row.get(key) for key in ['url', 'title', 'trading_date', 'duplicate_cluster_id']}, ensure_ascii=False))
