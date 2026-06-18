"""수정 검증 — sector/nav/macro + safe_index_ohlcv 견고성"""
import ssl, urllib3, requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _nv(self, *a, **k):
    k.setdefault("verify", False); return _orig(self, *a, **k)
requests.Session.request = _nv
from dotenv import load_dotenv
load_dotenv()

DATE = "20260612"
ok = True

print("=" * 60)
print("[1] safe_index_ohlcv 견고성")
from tools.market import safe_index_ohlcv
for name, code in [("KOSPI", "1001"), ("전기전자", "1013"), ("존재안함", "9999")]:
    df = safe_index_ohlcv(DATE, DATE, code)
    status = "빈DF(graceful)" if df.empty else f"종가={float(df.iloc[0]['종가'])}"
    print(f"  {name}({code}): {status}")
    if code != "9999" and df.empty:
        ok = False; print("    !! 정상코드인데 빈 DF")

print("\n[2] sector.get_sector_comparison")
from tools.sector import get_sector_comparison
r = get_sector_comparison("402340", DATE, competitors=["035720", "000660", "005930"])
print(f"  company_pct={r.get('company_pct')} kospi_pct={r.get('kospi_pct')}")
print(f"  sector_index={r.get('sector_index')}")
print(f"  competitors={r.get('competitors')}")
if not r.get("sector_index") or r["sector_index"].get("pct") is None:
    ok = False; print("    !! sector_index 누락")

print("\n[3] nav.get_nav_data — 발행주식수 132,087,115 반영")
from tools.nav import get_nav_data
from memory.company_context import COMPANY_CONTEXT
nav = get_nav_data(DATE)
shares = COMPANY_CONTEXT["shares_outstanding"]
print(f"  shares_outstanding={shares:,}")
print(f"  market_cap={nav.get('market_cap'):,} ({nav.get('market_cap_trillion')}조)")
print(f"  nav_total_trillion={nav.get('nav_total_trillion')}조")
print(f"  nav_discount_pct={nav.get('nav_discount_pct')}% / divergence={nav.get('divergence')}%p")
# market_cap == 종가 × shares 검증
from pykrx import stock
close = int(stock.get_market_ohlcv(DATE, DATE, "402340").iloc[0]["종가"])
expect = close * shares
print(f"  검증: 종가{close:,} × {shares:,} = {expect:,} | 일치={expect == nav.get('market_cap')}")
if expect != nav.get("market_cap"):
    ok = False

print("\n[4] macro.get_macro_data — KOSPI")
from tools.macro import get_macro_data
m = get_macro_data(DATE)
print(f"  kospi={m.get('kospi')}")
if not m.get("kospi") or m["kospi"].get("pct_change") is None:
    ok = False; print("    !! KOSPI 누락")

print("\n" + "=" * 60)
print("결과:", "✅ ALL PASS" if ok else "❌ 일부 실패")
