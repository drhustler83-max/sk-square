"""
Sector Comparison Tool
pykrx 업종 지수 + FinanceDataReader 경쟁사 비교
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
import FinanceDataReader as fdr
import pandas as pd
from loguru import logger

from tools.market import _prev_trading_day, safe_index_ohlcv
from memory.company_context import COMPANY_CONTEXT


def _index_pct(date: str, code: str) -> float | None:
    """KRX 업종지수 등락률 (당일/전일 종가 기준). 실패 시 None."""
    today = safe_index_ohlcv(date, date, code)
    prev  = safe_index_ohlcv(_prev_trading_day(date), _prev_trading_day(date), code)
    if today.empty or prev.empty:
        return None
    c_today = float(today.iloc[0]["종가"])
    c_prev  = float(prev.iloc[0]["종가"])
    if not c_prev:
        return None
    return round((c_today / c_prev - 1) * 100, 2)


def get_sector_comparison(ticker: str, date: str,
                          competitors: list[str] = None) -> dict:
    """
    Args:
        ticker: 자사 종목코드
        date: 'YYYYMMDD'
        competitors: 경쟁사 종목코드 리스트 (없으면 동일 섹터 자동 조회)
    """
    result = {}

    # 1. 자사 등락률
    try:
        ohlcv = stock.get_market_ohlcv(date, date, ticker)
        if not ohlcv.empty:
            result["company_pct"] = round(float(ohlcv.iloc[0]["등락률"]), 2)
    except Exception as e:
        logger.warning(f"Company OHLCV error: {e}")

    # 2. KOSPI 등락률 (get_index_ohlcv는 "등락률" 컬럼 없음 → 전일 대비 직접 계산)
    try:
        kospi_pct = _index_pct(date, "1001")
        if kospi_pct is not None:
            result["kospi_pct"] = kospi_pct
            logger.info(f"KOSPI: {result['kospi_pct']:+.2f}%")
    except Exception as e:
        logger.warning(f"KOSPI index error: {e}")

    # 2-1. 참조 섹터지수 등락률 (전기전자 등 — company_context에서 설정)
    sec = COMPANY_CONTEXT.get("sector_index")
    if sec:
        try:
            sec_pct = _index_pct(date, sec["code"])
            result["sector_index"] = {
                "name": sec["name"],
                "code": sec["code"],
                "pct":  sec_pct,
            }
            if sec_pct is not None:
                logger.info(f"섹터지수 {sec['name']}({sec['code']}): {sec_pct:+.2f}%")
        except Exception as e:
            logger.warning(f"섹터지수 error: {e}")

    # 3. 경쟁사 비교
    if competitors:
        comp_data = {}
        for comp_ticker in competitors[:5]:  # 최대 5개
            try:
                comp_ohlcv = stock.get_market_ohlcv(date, date, comp_ticker)
                if not comp_ohlcv.empty:
                    name = stock.get_market_ticker_name(comp_ticker)
                    comp_data[name] = round(float(comp_ohlcv.iloc[0]["등락률"]), 2)
            except Exception as e:
                logger.warning(f"Competitor {comp_ticker} error: {e}")
        result["competitors"] = comp_data

    # 4. 상대 수익률 (자사 - KOSPI)
    if "company_pct" in result and "kospi_pct" in result:
        result["relative_performance"] = round(
            result["company_pct"] - result["kospi_pct"], 2
        )

    return result
