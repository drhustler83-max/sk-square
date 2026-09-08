import os, ssl, json
import urllib3, requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _nv(self, *a, **k):
    k.setdefault("verify", False)
    return _orig(self, *a, **k)
requests.Session.request = _nv
from dotenv import load_dotenv
load_dotenv()

# [A] DART 발행주식 총수 전체 필드 덤프 (2025 사업보고서)
key = os.getenv("DART_API_KEY")
u = f"https://opendart.fss.or.kr/api/stockTotqySttus.json?crtfc_key={key}&corp_code=01596425&bsns_year=2025&reprt_code=11011"
resp = requests.get(u, timeout=30, verify=False).json()
print("[A] DART stockTotqySttus 2025 사업보고서 — 보통주 전체 필드")
for it in resp.get("list", []):
    if it.get("se", "").strip() in ("보통주", "합계"):
        print(f"  구분={it.get('se')}")
        for k2, v2 in it.items():
            print(f"     {k2} = {v2}")
        print()

# [B] KRX KOSPI 업종지수 코드 → 이름 매핑
from pykrx import stock
print("[B] KRX KOSPI 업종지수 전체 코드 목록")
for code in stock.get_index_ticker_list(market="KOSPI"):
    try:
        nm = stock.get_index_ticker_name(code)
        print(f"  {code} = {nm}")
    except Exception as e:
        print(f"  {code} = ERROR {e}")
