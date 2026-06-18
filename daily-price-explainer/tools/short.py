"""
Short Selling Tool
pykrx를 통해 공매도 잔고 및 거래량 수집

공매도 잔고 급증 → "원인 불명" 잔차의 구조적 설명변수
  - 잔고 증가 + 현물 하락 = 공매도 압력
  - 잔고 감소 + 현물 상승 = 공매도 청산(숏커버링)

※ KRX 공매도 잔고는 T+1 공시: 당일 날짜로 조회 시 데이터 없음.
   → balance_date = _prev_trading_day(date) 사용
   → "전일 잔고" = balance_date, "전전일 잔고" = prev_of_balance_date (변화량 계산용)
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

from pykrx import stock
from loguru import logger


def _find_col(df, candidates: list) -> str | None:
    """컬럼명 방어적 매칭 (부분 문자열)"""
    for c in candidates:
        for col in df.columns:
            if c in col:
                return col
    return None


def get_shorting_data(ticker: str, date: str) -> dict:
    """
    Args:
        ticker: 종목코드 (e.g. '402340')
        date:   'YYYYMMDD' — 조회 기준일 (당일). 잔고는 T+1이므로 내부에서 전일로 조정.
    Returns:
        balance_date:           실제 잔고 기준일 (전일)
        shorting_balance:       공매도 잔고수량 (주)
        shorting_balance_ratio: 공매도 잔고율 (%, 발행주식 대비)
        shorting_volume:        전일 공매도 거래량 (주)
        shorting_volume_ratio:  전일 공매도 비중 (%, 총거래량 대비)
        balance_change:         전일 vs 전전일 잔고 변화 (주, 양수=증가)
        signal:                 "압력" | "청산" | "중립" | None
    """
    from tools.market import _prev_trading_day

    # 공매도 잔고는 T+2 공시: 전전일부터 역방향으로 최대 3일 시도
    balance_date = None
    df_bal_found = None
    _d = _prev_trading_day(date)
    for _ in range(3):
        try:
            _df = stock.get_shorting_balance_by_date(_d, _d, ticker)
            if _df is not None and not _df.empty:
                balance_date = _d
                df_bal_found = _df
                break
        except Exception:
            pass
        _d = _prev_trading_day(_d)

    if balance_date is None:
        balance_date = _prev_trading_day(date)   # 표시용 fallback
    prev_balance_date = _prev_trading_day(balance_date)

    result: dict = {"balance_date": balance_date}

    # ── 1. 공매도 잔고 (balance_date 기준, 위에서 이미 탐색 완료) ─────────────
    if df_bal_found is not None:
        row = df_bal_found.iloc[0]
        col_qty   = _find_col(df_bal_found, ["잔고수량", "잔고"])
        col_ratio = _find_col(df_bal_found, ["잔고율", "공매도비율", "비중", "비율"])
        if col_qty:
            result["shorting_balance"] = int(row[col_qty])
        if col_ratio:
            result["shorting_balance_ratio"] = round(float(row[col_ratio]), 2)
        logger.info(
            f"공매도 잔고({balance_date}) — "
            f"{result.get('shorting_balance', 'N/A'):,}주 "
            f"({result.get('shorting_balance_ratio', 'N/A')}%)"
        )
    else:
        logger.warning(f"공매도 잔고 데이터 없음: {ticker} (최근 3일 조회 실패)")

    # ── 2. 공매도 거래량 (balance_date 기준) ─────────────────────────────────
    try:
        df_vol = stock.get_shorting_volume_by_date(balance_date, balance_date, ticker)
        if df_vol is not None and not df_vol.empty:
            row = df_vol.iloc[0]
            col_sv    = _find_col(df_vol, ["공매도", "공매"])
            col_ratio = _find_col(df_vol, ["비중", "비율"])

            if col_sv:
                result["shorting_volume"] = int(row[col_sv])
            if col_ratio:
                result["shorting_volume_ratio"] = round(float(row[col_ratio]), 2)

            logger.info(
                f"공매도 거래량({balance_date}) — "
                f"{result.get('shorting_volume', 'N/A'):,}주 "
                f"(비중 {result.get('shorting_volume_ratio', 'N/A')}%)"
            )
        else:
            logger.warning(f"공매도 거래량 데이터 없음: {ticker} {balance_date}")
    except Exception as e:
        logger.warning(f"get_shorting_volume_by_date 오류: {e}")

    # ── 3. 전전일 잔고 → 변화량 계산 ─────────────────────────────────────────
    try:
        df_prev = stock.get_shorting_balance_by_date(prev_balance_date, prev_balance_date, ticker)
        if df_prev is not None and not df_prev.empty:
            col_qty_p = _find_col(df_prev, ["잔고수량", "잔고"])
            if col_qty_p:
                prev_bal = int(df_prev.iloc[0][col_qty_p])
                result["prev_balance"] = prev_bal
                if "shorting_balance" in result:
                    chg = result["shorting_balance"] - prev_bal
                    result["balance_change"] = chg
                    logger.info(
                        f"공매도 잔고 변화: {chg:+,}주 "
                        f"({prev_balance_date} {prev_bal:,}주 → {balance_date} {result['shorting_balance']:,}주)"
                    )
    except Exception as e:
        logger.warning(f"전전일 잔고 조회 오류: {e}")

    # ── 4. 시그널 판정 ─────────────────────────────────────────────────────
    chg   = result.get("balance_change")
    ratio = result.get("shorting_balance_ratio")
    if chg is not None and ratio is not None:
        if chg > 5000 and ratio >= 1.0:
            result["signal"] = "압력"    # 잔고 증가 + 비율 유의미 → 공매도 하방 압력
        elif chg < -5000:
            result["signal"] = "청산"   # 잔고 감소 → 숏커버링(매수 유입 가능)
        else:
            result["signal"] = "중립"
    else:
        result["signal"] = None

    return result
