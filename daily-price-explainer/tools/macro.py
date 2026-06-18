"""
Macro Tool
BOK ECOS (환율·금리) + Naver Finance (글로벌 지수·KOSPI)
"""
import os
import re
import ssl
import httpx
import urllib3
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from loguru import logger

# pykrx SSL 패치 (KOSPI 지수용)
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

# 사내망 SSL 프록시 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
HTTP_CLIENT = httpx.Client(verify=False)

BOK_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

def _prev_trading_day(date_str: str) -> str:
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y%m%d")
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")

# BOK ECOS 시계열 코드
BOK_SERIES = {
    "usd_krw":   ("731Y001", "0000001"),   # 원달러 환율
    "bond_3y":   ("817Y002", "010190000"), # 국고채 3년
    "base_rate": ("722Y001", "0101000"),   # 기준금리
}

# Naver Finance 해외지수 심볼
NAVER_WORLD_SYMBOLS = {
    "sp500":  "SPI@SPX",
    "nasdaq": "NAS@IXIC",
}


def get_macro_data(date: str) -> dict:
    """
    Args:
        date: 'YYYYMMDD'
    """
    result = {}
    result.update(_get_bok_data(date))
    result.update(_get_kospi(date))
    result.update(_get_yf_data(date))
    return result


def _get_kospi(date: str) -> dict:
    """pykrx로 KOSPI 지수 수집 (종목코드 '1001')"""
    try:
        from tools.market import safe_index_ohlcv
        df = safe_index_ohlcv(date, date, "1001")
        if df.empty:
            logger.warning(f"KOSPI pykrx 데이터 없음: {date}")
            return {}
        row = df.iloc[0]
        close = float(row.get("종가", row.iloc[3]))

        # 전일 종가로 등락률 계산
        prev_date = _prev_trading_day(date)
        df_prev = safe_index_ohlcv(prev_date, prev_date, "1001")
        pct = None
        if not df_prev.empty:
            prev_close = float(df_prev.iloc[0].get("종가", df_prev.iloc[0].iloc[3]))
            if prev_close:
                pct = round((close / prev_close - 1) * 100, 2)

        logger.info(f"KOSPI: {close} ({pct:+.2f}%)" if pct is not None else f"KOSPI: {close}")
        return {"kospi": {"close": close, "pct_change": pct}}
    except Exception as e:
        logger.warning(f"KOSPI pykrx 오류: {e}")
        return {}


def _get_bok_data(date: str) -> dict:
    api_key = os.getenv("BOK_API_KEY")
    if not api_key:
        return {"bok_error": "BOK_API_KEY 미설정"}

    out = {}
    for name, (stat_code, item_code) in BOK_SERIES.items():
        try:
            url = (f"{BOK_BASE}/{api_key}/json/kr/1/5/"
                   f"{stat_code}/D/{date}/{date}/{item_code}")
            r = HTTP_CLIENT.get(url, timeout=10)
            r.raise_for_status()
            rows = r.json().get("StatisticSearch", {}).get("row", [])
            if rows:
                out[name] = float(rows[-1]["DATA_VALUE"].replace(",", ""))
        except Exception as e:
            logger.warning(f"BOK {name} error: {e}")
            out[name] = None

    return out


def _get_yf_data(date: str) -> dict:
    """Naver Finance 해외지수 파싱 (S&P500, NASDAQ)"""
    out = {}
    naver_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    for name, symbol in NAVER_WORLD_SYMBOLS.items():
        try:
            url = f"https://finance.naver.com/world/sise.naver?symbol={symbol}"
            r = requests.get(url, headers=naver_headers, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            rate_div = soup.find(class_="rate_info")
            if not rate_div:
                continue
            text = rate_div.get_text()
            # 현재가: 첫 번째 숫자 그룹
            close_m = re.search(r'^([\d,]+\.?\d*)', text.strip())
            # 등락률: ( -1.62% ) — 괄호 안 개행/공백 허용, 유니코드 마이너스 포함
            text_norm = text.replace('−', '-')
            pct_m = re.search(r'\(\s*([-+]?\s*[\d.]+)\s*%\s*\)', text_norm, re.DOTALL)
            if close_m:
                close = float(close_m.group(1).replace(",", ""))
                pct   = float(pct_m.group(1).replace(" ", "").replace("\n", "")) if pct_m else None
                out[name] = {"close": close, "pct_change": pct}
                logger.info(f"Naver {name}: {close} ({pct:+.2f}%)" if pct else f"Naver {name}: {close}")
        except Exception as e:
            logger.warning(f"Naver {name} 오류: {e}")

    return out
