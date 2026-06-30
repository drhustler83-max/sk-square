"""
extra_log.csv 백필 — Tier1/2 추가 피처용 원천 데이터.

단일 벌크 호출 2개로 전 구간 생성 (factor_log/futures_log와 동일하게 features.py에서 date join):
  · foreign_own_pct  외국인 지분율(%)  ← get_exhaustion_rates_of_foreign_investment
  · individual_net   개인 순매수(거래대금, 원) ← get_market_trading_value_by_date

사용:
  python tools/backfill_extra.py            # 전 구간(20211101~오늘) 재생성
"""
import ssl
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

from pathlib import Path
from datetime import datetime
from pykrx import stock
from loguru import logger

EXTRA_PATH = Path(__file__).parent.parent / "data" / "extra_log.csv"
DEFAULT_START = "20211101"


def backfill_extra(ticker: str = "402340", start: str = DEFAULT_START, end: str = None):
    import pandas as pd
    end = end or datetime.today().strftime("%Y%m%d")

    # 1) 외국인 지분율 (%) — 전 구간 시계열 1콜
    own = stock.get_exhaustion_rates_of_foreign_investment(start, end, ticker)
    own = own[["지분율"]].rename(columns={"지분율": "foreign_own_pct"})

    # 2) 개인 순매수 (거래대금, 원) — per-date 투자자 분해 1콜
    val = stock.get_market_trading_value_by_date(start, end, ticker)
    ind = val[["개인"]].rename(columns={"개인": "individual_net"})

    out = own.join(ind, how="outer").sort_index()
    out.index.name = "date"
    out = out.reset_index()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")

    EXTRA_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(EXTRA_PATH, index=False, encoding="utf-8-sig")
    logger.info(f"extra_log 저장: {len(out)}행 ({out['date'].iloc[0]}~{out['date'].iloc[-1]}) → {EXTRA_PATH}")
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(str(Path(__file__).parent.parent / ".env"))
    df = backfill_extra()
    print(df.tail(6).to_string())
