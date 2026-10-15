"""Inspect public HTML, save only metadata and hashes; never archive article bodies."""
import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HERE = Path(__file__).resolve().parent
SELECTORS = {
    "www.newsis.com": '[itemprop="articleBody"]',
    "www.mt.co.kr": '[itemprop="articleBody"]',
    "www.newspim.com": '#news-contents',
    "www.digitaltoday.co.kr": '#article-view-content-div',
    "www.mk.co.kr": '.news_cnt_detail_wrap',
    "www.hankyung.com": '#articletxt',
    "www.donga.com": '.article_txt',
}

def fetch(spec):
    url = spec['url']
    receipt = {'url': url, 'retrieved_at': datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds'), 'body_hash': None}
    try:
        # This workspace's HTTPS proxy is not trusted by requests/certifi.
        # Public read-only receipts only; preserve this limitation explicitly.
        response = requests.get(url, timeout=25, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        receipt['certificate_verification'] = False
        receipt['http_status'] = response.status_code
        receipt['resolved_url'] = response.url
        if response.status_code != 200:
            return receipt
        soup = BeautifulSoup(response.content, 'html.parser')
        receipt['page_title'] = soup.title.get_text(' ', strip=True) if soup.title else None
        receipt['canonical_url'] = next((link.get('href') for link in soup.select('link[rel="canonical"]')), None)
        receipt['date_metadata'] = [{k: m.get(k) for k in ('property', 'name', 'content') if m.get(k)} for m in soup.select('meta') if any(s in (m.get('property', '') + ' ' + m.get('name', '')).lower() for s in ('date', 'time'))]
        selector = SELECTORS.get(urlparse(url).netloc)
        body = soup.select_one(selector) if selector else None
        if body:
            for bad in body.select('script,style,iframe'):
                bad.decompose()
            value = ' '.join(body.get_text(' ', strip=True).split())
            if len(value) > 150 and '\ufffd' not in value:
                receipt.update(body_hash=hashlib.sha256(value.encode('utf-8')).hexdigest(), body_chars=len(value), selector=selector, hash_recipe='SHA256(UTF8(collapsed-whitespace selected article container text)); script/style/iframe removed')
        if not selector or not body:
            receipt['container_candidates'] = [{'tag': node.name, 'id': node.get('id'), 'class': node.get('class')} for node in soup.select('[id*=article], [class*=article], [id*=content], [class*=content]')][:45]
    except Exception as error:
        receipt['error'] = type(error).__name__
    return receipt

if __name__ == '__main__':
    specs = json.loads((HERE / 'specs.json').read_text(encoding='utf-8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(fetch, specs['articles']))
    (HERE / 'receipts.json').write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for receipt in receipts:
        print(json.dumps(receipt, ensure_ascii=False))
