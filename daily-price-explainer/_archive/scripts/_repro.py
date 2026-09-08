"""임시 진단 스크립트 — 미해결 과제 2건 재현"""
import os, sys, ssl, json
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
sys.path.insert(0, os.path.dirname(__file__))

DATE = "20260612"

print("=" * 60)
print("[1] sector.get_sector_comparison 재현")
print("=" * 60)
try:
    from tools.sector import get_sector_comparison
    r = get_sector_comparison("402340", DATE, competitors=["035720", "000660", "005930"])
    print(json.dumps(r, ensure_ascii=False, indent=2))
except Exception as e:
    import traceback; traceback.print_exc()

print("\n" + "=" * 60)
print("[2] KRX 섹터 지수(반도체 1028) 직접 조회 가능 여부")
print("=" * 60)
try:
    from pykrx import stock
    for name, code in {"KOSPI": "1001", "반도체": "1028", "IT하드웨어": "1022", "통신": "1016", "지주": "1006"}.items():
        try:
            df = stock.get_index_ohlcv(DATE, DATE, code)
            if df.empty:
                print(f"  {name}({code}): 빈 데이터")
            else:
                print(f"  {name}({code}): 종가={float(df.iloc[0]['종가'])} 컬럼={list(df.columns)}")
        except Exception as e:
            print(f"  {name}({code}): ERROR {type(e).__name__}: {e}")
except Exception as e:
    import traceback; traceback.print_exc()

print("\n" + "=" * 60)
print("[3] DART 발행주식수 (stockTotqySttus)")
print("=" * 60)
key = os.getenv("DART_API_KEY")
corp = "01596425"
import requests as rq
for y in (2026, 2025):
    for rcode in ("11013", "11011", "11014", "11012"):
        u = f"https://opendart.fss.or.kr/api/stockTotqySttus.json?crtfc_key={key}&corp_code={corp}&bsns_year={y}&reprt_code={rcode}"
        try:
            resp = rq.get(u, timeout=30, verify=False).json()
            if resp.get("status") == "000":
                print(f"\n--- bsns_year={y} reprt_code={rcode} ---")
                for it in resp.get("list", []):
                    print(f"  se={it.get('se')} | 발행총수={it.get('isu_stock_totqy')} | "
                          f"유통주식={it.get('distb_stock_co')} | 자기주식={it.get('tesstk_co')}")
            else:
                print(f"  y={y} r={rcode} -> {resp.get('status')}: {resp.get('message')}")
        except Exception as e:
            print(f"  y={y} r={rcode} -> ERROR {e}")
