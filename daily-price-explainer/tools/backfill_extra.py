"""
data/factor_log.csv의 foreign_own_pct/individual_net 컬럼 채움 (Tier1/2 추가 피처 원천).

2026-07-21: 별도 파일(extra_log.csv)을 factor_log.csv에 병합 후 폐기 — 이 스크립트도
이제 factor_log.csv를 직접 읽고 갱신한다.

단일 벌크 호출 2개로 전 구간 재조회 후 factor_log.csv에 date 기준으로 반영한다
(지분율·개인 순매수는 정정 공시가 있을 수 있어 매번 전 구간 재조회가 안전):
  · foreign_own_pct  외국인 지분율(%)  ← get_exhaustion_rates_of_foreign_investment
  · individual_net   개인 순매수(거래대금, 원) ← get_market_trading_value_by_date

사용:
  python tools/backfill_extra.py            # 전 구간(20211101~오늘) 재조회
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

import pandas as pd
from pykrx import stock
from loguru import logger

LOG_PATH = Path(__file__).parent.parent / "data" / "factor_log.csv"
DEFAULT_START = "20211101"


def backfill_extra(ticker: str = "402340", start: str = DEFAULT_START, end: str = None) -> pd.DataFrame:
    end = end or datetime.today().strftime("%Y%m%d")

    own = stock.get_exhaustion_rates_of_foreign_investment(start, end, ticker)
    own = own[["지분율"]].rename(columns={"지분율": "foreign_own_pct"})

    val = stock.get_market_trading_value_by_date(start, end, ticker)
    ind = val[["개인"]].rename(columns={"개인": "individual_net"})

    extra = own.join(ind, how="outer").sort_index().reset_index()
    extra.columns = ["date", "foreign_own_pct", "individual_net"]
    extra["date"] = pd.to_datetime(extra["date"]).dt.strftime("%Y%m%d")

    if not Path(LOG_PATH).exists():
        logger.error("factor_log.csv 없음. 중단.")
        return extra

    df = pd.read_csv(LOG_PATH, dtype=str)
    for c in ("foreign_own_pct", "individual_net"):
        if c not in df.columns:
            df[c] = ""

    date_to_idx = {d: i for i, d in enumerate(df["date"])}
    n_updated = 0
    for _, r in extra.iterrows():
        idx = date_to_idx.get(r["date"])
        if idx is None:
            continue
        own_val, ind_val = r["foreign_own_pct"], r["individual_net"]
        df.at[idx, "foreign_own_pct"] = "" if pd.isna(own_val) else str(own_val)
        df.at[idx, "individual_net"]  = "" if pd.isna(ind_val) else str(int(ind_val))
        n_updated += 1

    df.to_csv(LOG_PATH, index=False, na_rep="")
    logger.info(
        f"factor_log.csv 갱신: foreign_own_pct/individual_net {n_updated}행 "
        f"({extra['date'].min()}~{extra['date'].max()})"
    )
    return df


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(str(Path(__file__).parent.parent / ".env"))
    df = backfill_extra()
    print(df.tail(6).to_string())
