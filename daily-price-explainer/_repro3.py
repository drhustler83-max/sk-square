import ssl, urllib3, requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _nv(self, *a, **k):
    k.setdefault("verify", False); return _orig(self, *a, **k)
requests.Session.request = _nv
from pykrx import stock

# [A] KRX 상장주식수 (시가총액 산정 기준)
print("[A] pykrx 시가총액/상장주식수 (402340, 20260612)")
cap = stock.get_market_cap("20260612", "20260612", "402340")
print(cap.to_string())
print("  컬럼:", list(cap.columns))

# [B] 전기전자(1013) 섹터 지수 — KOSPI와 같은 방식 검증
print("\n[B] 전기전자(1013) 지수 전일대비")
for d in ("20260611", "20260612"):
    df = stock.get_index_ohlcv(d, d, "1013")
    print(f"  {d}: {'빈데이터' if df.empty else float(df.iloc[0]['종가'])}")
