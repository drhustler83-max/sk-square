import ssl, urllib3, requests
urllib3.disable_warnings()
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *a, **kw):
    kw.setdefault("verify", False)
    return _orig(self, *a, **kw)
requests.Session.request = _no_verify

from dotenv import load_dotenv
load_dotenv()

from pykrx import stock

SSF = "KRDRVFUEQU"
DATE = "20260612"

# ── 1. SK스퀘어 계약 찾기 ─────────────────────────────────────────
print("=== SK스퀘어 주식선물 계약 조회 ===")
df = stock.get_future_ohlcv_by_ticker(DATE, SSF)
sq = df[df["종목명"].str.contains("스퀘어", na=False)]
print(f"SK스퀘어 계약 수: {len(sq)}")
print(sq.to_string())

# ── 2. get_future_ohlcv 함수 시그니처·결과 탐색 ──────────────────
print("\n=== get_future_ohlcv 탐색 ===")
if len(sq) > 0:
    # 최근월물 (거래량 최대) 티커 추출
    near_ticker = sq["거래량"].idxmax()
    near_name   = sq.loc[near_ticker, "종목명"]
    print(f"최근월물: {near_ticker} / {near_name}")

    # 날짜범위로 OHLCV + 미결제약정 시도
    for args in [
        (DATE, DATE, near_ticker),
        (near_ticker, DATE, DATE),
        ("20260601", DATE, near_ticker),
    ]:
        try:
            r = stock.get_future_ohlcv(*args)
            print(f"  get_future_ohlcv{args} → shape={r.shape}, cols={r.columns.tolist()}")
            print(r.tail(3).to_string())
            break
        except Exception as e:
            print(f"  get_future_ohlcv{args} → 실패: {e}")
else:
    print("SK스퀘어 계약 없음 — 확인용으로 스퀘어/SK 검색:")
    for kw in ["SK", "스퀘어", "402340", "square"]:
        hits = df[df["종목명"].str.contains(kw, case=False, na=False)]
        if not hits.empty:
            print(f"  '{kw}' 검색 결과: {len(hits)}건")
            print(hits[["종목명","종가","거래량"]].head(5).to_string())
