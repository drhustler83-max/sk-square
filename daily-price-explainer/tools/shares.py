"""
Listed Shares Tool
pykrx 상장주식수·시가총액 조회

SK스퀘어는 자사주 매입·소각을 반복해 상장주식수가 계속 변한다.
따라서 "목표주가 × 주식수" 같은 계산은 반드시 그 시점 주식수를 써야 한다.
(Phase 2 목표주가 내재 할인율 산출의 필수 입력)

원 출처: 루트 임시 스크립트 _repro3.py [A] 블록 → 정식 모듈로 승격 (2026-09-08)
"""
import ssl
import urllib3
import requests

# 사내망 SSL 프록시 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_original_request = requests.Session.request
def _no_verify_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _original_request(self, *args, **kwargs)
requests.Session.request = _no_verify_request

from pykrx import stock
import pandas as pd
from loguru import logger

TICKER_DEFAULT = "402340"          # SK스퀘어
_SHARES_COL = "상장주식수"
_CAP_COL    = "시가총액"


def get_listed_shares(date: str, ticker: str = TICKER_DEFAULT) -> int | None:
    """해당 거래일의 상장주식수. 비거래일·조회 실패 시 None.

    Args:
        date: 'YYYYMMDD'
        ticker: 종목코드
    """
    try:
        cap = stock.get_market_cap(date, date, ticker)
    except Exception as e:
        logger.warning(f"상장주식수 조회 실패 {ticker}@{date}: {e}")
        return None
    if cap.empty or _SHARES_COL not in cap.columns:
        return None
    return int(cap.iloc[0][_SHARES_COL])


def get_market_cap(date: str, ticker: str = TICKER_DEFAULT) -> dict:
    """해당 거래일의 시가총액·상장주식수를 함께 반환. 실패 시 빈 dict."""
    try:
        cap = stock.get_market_cap(date, date, ticker)
    except Exception as e:
        logger.warning(f"시가총액 조회 실패 {ticker}@{date}: {e}")
        return {}
    if cap.empty:
        return {}
    row = cap.iloc[0]
    return {
        "date":          date,
        "market_cap":    int(row[_CAP_COL])    if _CAP_COL    in cap.columns else None,
        "listed_shares": int(row[_SHARES_COL]) if _SHARES_COL in cap.columns else None,
    }


def get_shares_series(start: str, end: str,
                      ticker: str = TICKER_DEFAULT) -> pd.DataFrame:
    """기간 내 일별 상장주식수·시가총액.

    상장주식수가 줄어드는 지점이 자사주 소각 시점이므로,
    이 시계열의 계단 하락을 보면 소각 이력을 역추적할 수 있다.

    Returns:
        컬럼 [date, listed_shares, market_cap]. 실패 시 빈 DataFrame.
    """
    try:
        cap = stock.get_market_cap(start, end, ticker)
    except Exception as e:
        logger.warning(f"상장주식수 시계열 조회 실패 {ticker} {start}~{end}: {e}")
        return pd.DataFrame(columns=["date", "listed_shares", "market_cap"])
    if cap.empty:
        return pd.DataFrame(columns=["date", "listed_shares", "market_cap"])

    out = pd.DataFrame({
        "date": cap.index.strftime("%Y%m%d"),
        "listed_shares": cap[_SHARES_COL] if _SHARES_COL in cap.columns else None,
        "market_cap":    cap[_CAP_COL]    if _CAP_COL    in cap.columns else None,
    }).reset_index(drop=True)
    return out


def get_share_reduction_events(start: str, end: str,
                               ticker: str = TICKER_DEFAULT) -> pd.DataFrame:
    """상장주식수가 감소한 날 = 자사주 소각 반영일 후보.

    DART 소각 공시와 대조하는 용도이며, 이 자체를 공시 시점으로 쓰면 안 된다
    (반영일 ≠ 공개일 — schemas/f6_event_protocol.md 의 known_at 규칙 참조).

    Returns:
        컬럼 [date, listed_shares, prev_shares, delta_shares, delta_pct].
    """
    series = get_shares_series(start, end, ticker)
    if series.empty:
        return pd.DataFrame(
            columns=["date", "listed_shares", "prev_shares", "delta_shares", "delta_pct"]
        )

    series = series.dropna(subset=["listed_shares"]).copy()
    series["prev_shares"] = series["listed_shares"].shift(1)
    series["delta_shares"] = series["listed_shares"] - series["prev_shares"]
    events = series[series["delta_shares"] < 0].copy()
    if events.empty:
        return pd.DataFrame(
            columns=["date", "listed_shares", "prev_shares", "delta_shares", "delta_pct"]
        )
    events["delta_pct"] = (
        events["delta_shares"] / events["prev_shares"] * 100
    ).round(3)
    cols = ["date", "listed_shares", "prev_shares", "delta_shares", "delta_pct"]
    return events[cols].reset_index(drop=True)
