"""
Market Agent Tool
pykrx를 통해 주가 OHLCV + 외국인/기관/개인 순매수 수집
"""
import ssl
import urllib3
import requests

# 사내망 SSL 프록시 우회 (pykrx는 requests 사용)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_original_request = requests.Session.request
def _no_verify_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _original_request(self, *args, **kwargs)
requests.Session.request = _no_verify_request

from pykrx import stock
import pandas as pd
import re
import requests as _requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from loguru import logger


_NAVER_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _get_kospi_investor_flow() -> dict:
    """Naver Finance sise_index에서 KOSPI 투자자별 매매동향 파싱 (단위: 억원)"""
    url = "https://finance.naver.com/sise/sise_index.naver?code=KOSPI"
    r = _requests.get(url, headers=_NAVER_H, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")

    result = {}
    for dl in soup.find_all("dl"):
        txt = dl.get_text(" | ", strip=True)
        if "외국인" not in txt or "기관" not in txt:
            continue
        # 패턴: '개인 | +20,787 | 억 | 외국인 | -14,636 | 억 | 기관 | -7,566 | 억'
        for label, key in [("외국인", "foreign_net"), ("기관", "institution_net"), ("개인", "individual_net")]:
            m = re.search(rf'{label}\s*\|\s*([+\-−]?[\d,]+)\s*\|\s*억', txt)
            if m:
                val_str = m.group(1).replace(",", "").replace("−", "-")
                result[key] = int(val_str)
        break

    return result


def _safe_int(val):
    try:
        return int(val) if val is not None else None
    except Exception:
        return None


def get_market_data(ticker: str, date: str) -> dict:
    """
    Args:
        ticker: 종목코드 (e.g. '402340')
        date: 'YYYYMMDD' 형식
    Returns:
        dict with ohlcv + investor flows
    """
    try:
        # OHLCV — 오늘 데이터 없으면(장 시작 전/휴장) 전일로 fallback
        ohlcv = stock.get_market_ohlcv(date, date, ticker)
        is_prev_day = False
        actual_date = date
        if ohlcv.empty:
            prev = _prev_trading_day(date)
            ohlcv = stock.get_market_ohlcv(prev, prev, ticker)
            if ohlcv.empty:
                logger.warning(f"No OHLCV data for {ticker} on {date} or {prev}")
                return {"error": "데이터 없음 (휴장일이거나 상장 전)"}
            logger.info(f"{ticker} 오늘 데이터 없음 → 전일({prev}) 종가 사용")
            is_prev_day = True
            actual_date = prev

        row = ohlcv.iloc[0].to_dict()  # Series → dict, KeyError 방지
        prev_date = _prev_trading_day(date)
        prev_ohlcv = stock.get_market_ohlcv(prev_date, prev_date, ticker)
        prev_close = float(prev_ohlcv.iloc[0]["종가"]) if not prev_ohlcv.empty else None

        # 수급 (외국인/기관/개인)
        try:
            flows = stock.get_market_trading_value_by_investor(date, date, ticker)
        except Exception:
            flows = pd.DataFrame()

        close = row.get("종가", 0)
        result = {
            "date":         actual_date,
            "is_prev_day":  is_prev_day,   # True = 전일 종가 기준 (장 시작 전)
            "ticker": ticker,
            "open":          _safe_int(row.get("시가")),
            "high":          _safe_int(row.get("고가")),
            "low":           _safe_int(row.get("저가")),
            "close":         _safe_int(close),
            "volume":        _safe_int(row.get("거래량")),
            "trading_value": _safe_int(row.get("거래대금")),
            "prev_close":    _safe_int(prev_close),
            "pct_change":    round((close / prev_close - 1) * 100, 2) if prev_close and close else None,
        }

        if not flows.empty:
            logger.debug(f"investor_flow 인덱스: {flows.index.tolist()}, 컬럼: {flows.columns.tolist()}")

            def _flow(labels):
                """라벨 후보 순서대로 시도, 없으면 None"""
                col = "순매수" if "순매수" in flows.columns else (
                      "거래량" if "거래량" in flows.columns else flows.columns[-1])
                for label in labels:
                    try:
                        v = flows.loc[label, col]
                        if v is not None and str(v) != "nan":
                            return int(v)
                    except Exception:
                        pass
                return None

            result["investor_flow"] = {
                "foreign_net":     _flow(["외국인", "외국인합계"]),   # "외국인" = 등록외국인 순계
                "institution_net": _flow(["기관합계", "기관"]),
                "individual_net":  _flow(["개인"]),
            }
            logger.info(
                f"SK스퀘어 수급 - 외국인: {result['investor_flow']['foreign_net']}, "
                f"기관: {result['investor_flow']['institution_net']}, "
                f"개인: {result['investor_flow']['individual_net']}"
            )

        # KOSPI 전체 외국인 순매수 (Naver Finance 파싱)
        try:
            result["kospi_investor_flow"] = _get_kospi_investor_flow()
            kf = result["kospi_investor_flow"]
            logger.info(
                f"KOSPI 수급 - 외국인: {kf['foreign_net']:+,}억, "
                f"기관: {kf['institution_net']:+,}억, 개인: {kf['individual_net']:+,}억"
            )
        except Exception as e:
            logger.warning(f"KOSPI 수급 오류: {e}")

        return result

    except Exception as e:
        logger.error(f"get_market_data error: {e}")
        return {"error": str(e)}


def _prev_trading_day(date_str: str) -> str:
    """가장 가까운 직전 영업일 반환 (간단 버전: 주말 제외)"""
    dt = datetime.strptime(date_str, "%Y%m%d")
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:  # 토(5), 일(6)
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


def safe_index_ohlcv(start: str, end: str, code: str) -> pd.DataFrame:
    """get_index_ohlcv 견고 래퍼.

    KRX 세션이 실패/만료되면 pykrx가 인덱스 티커 테이블을 비운 채 캐시하고,
    get_index_ohlcv 내부의 get_index_ticker_name(code)이 KeyError('지수명')를
    던지며 호출 전체가 죽는다(OHLCV 데이터 자체는 이미 받았는데 컬럼명 붙이는
    단계에서 크래시). 이를 흡수한다:
      1회 한해 인덱스 티커 캐시를 재적재(get_index_ticker_list) 후 재시도,
      그래도 실패하면 빈 DataFrame 반환(호출부는 .empty로 이미 처리).
    """
    for attempt in range(2):
        try:
            return stock.get_index_ohlcv(start, end, code)
        except KeyError as e:
            if "지수명" in str(e) and attempt == 0:
                logger.warning(f"index {code} '지수명' 캐시 비어있음 → 티커 캐시 재적재 후 재시도")
                try:
                    stock.get_index_ticker_list(date=end, market="KOSPI")
                except Exception as warm_err:
                    logger.warning(f"index 티커 캐시 워밍 실패: {warm_err}")
                continue
            logger.warning(f"index {code} 조회 실패: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"index {code} 조회 실패: {e}")
            return pd.DataFrame()
    return pd.DataFrame()
