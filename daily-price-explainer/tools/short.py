"""
Short Selling Tool
pykrx를 통해 공매도 잔고 및 거래량 수집

공매도 잔고 급증 → "원인 불명" 잔차의 구조적 설명변수
  - 잔고 증가 + 현물 하락 = 공매도 압력
  - 잔고 감소 + 현물 상승 = 공매도 청산(숏커버링)

원본은 요청한 실제 기준일 값으로 저장한다(Sean 결정, 2026-10-01).
공시 지연/조회 실패 시 결측을 유지하며 이전 날짜 값으로 대체하지 않는다.
과거 CSV의 레거시 지연 값은 소급 수정하지 않는다.
"""
from datetime import datetime
from decimal import Decimal

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
    """정확한 컬럼명만 허용해 잔고금액을 잔고수량으로 읽지 않는다."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _dated_row(df, date: str):
    """요청일의 유일한 행만 선택. 최신/첫 행으로 폴백하지 않는다."""
    if df is None or df.empty:
        return None
    dates = []
    for value in df.index:
        if hasattr(value, "strftime"):
            key = value.strftime("%Y%m%d")
        else:
            key = str(value).replace("-", "")
        dates.append(key)
    matches = [i for i, key in enumerate(dates) if key == date]
    if len(matches) != 1:
        raise ValueError(f"공매도 응답에 {date}의 유일한 행이 없습니다")
    return df.iloc[matches[0]]


def _quantity(value) -> int:
    number = Decimal(str(value).replace(",", ""))
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError("공매도 수량은 유한한 비음수 정수여야 합니다")
    return int(number)


def _ratio(value) -> float:
    number = Decimal(str(value).replace(",", ""))
    if not number.is_finite() or not 0 <= number <= 100:
        raise ValueError("공매도 비율은 0~100%의 유한한 값이어야 합니다")
    return round(float(number), 2)


def get_shorting_data(ticker: str, date: str) -> dict:
    """요청일 D의 잔고·거래비중, D와 직전 거래일의 잔고 차분을 반환.

    balance_date/volume_date는 조회 기준일이다. 빈 응답·오류·날짜 불일치는
    해당 값이 None이다. 포착한 예외는 errors에 남으며 빈 응답만으로 미공시를
    확정하지 않는다. 0은 정상 값이다.
    balance_change는 원천의 D 잔고와 직전 거래일 잔고가 둘 다 있어야 계산한다.
    레거시 CSV 값을 차분 계산에 사용하지 않는다.
    """
    if not isinstance(date, str) or len(date) != 8 or not date.isdecimal():
        raise ValueError("공매도 조회일은 YYYYMMDD여야 합니다")
    datetime.strptime(date, "%Y%m%d")
    result = {"balance_date": date, "volume_date": date, "prev_balance_date": None,
              "shorting_balance": None, "shorting_balance_ratio": None,
              "shorting_volume": None, "shorting_volume_ratio": None,
              "prev_balance": None, "balance_change": None, "signal": None,
              "errors": {}}

    # 잔고 조회와 거래량 조회는 독립적이다. 잔고 미공시여도 당일 거래비중은 받는다.
    try:
        df_bal = stock.get_shorting_balance_by_date(date, date, ticker)
        row = _dated_row(df_bal, date)
        if row is not None:
            col_qty = _find_col(df_bal, ["공매도잔고", "공매도잔고수량", "잔고수량", "잔고"])
            if col_qty is None:
                raise ValueError("공매도 잔고수량 컬럼이 없습니다")
            balance = _quantity(row[col_qty])
            col_ratio = _find_col(df_bal, ["잔고율", "공매도비율", "비중", "비율"])
            ratio = _ratio(row[col_ratio]) if col_ratio else None
            result["shorting_balance"] = balance
            result["shorting_balance_ratio"] = ratio
        else:
            logger.warning(f"공매도 잔고 미확보: {ticker} {date}")
    except Exception as exc:
        result["errors"]["balance"] = str(exc)
        logger.warning(f"공매도 잔고 조회 실패({date}): {exc}")

    try:
        df_vol = stock.get_shorting_volume_by_date(date, date, ticker)
        row = _dated_row(df_vol, date)
        if row is not None:
            col_sv = _find_col(df_vol, ["공매도", "공매도거래량", "공매도수량", "공매"])
            col_ratio = _find_col(df_vol, ["공매도비중", "비중", "비율"])
            volume = _quantity(row[col_sv]) if col_sv else None
            ratio = _ratio(row[col_ratio]) if col_ratio else None
            result["shorting_volume"] = volume
            result["shorting_volume_ratio"] = ratio
        else:
            logger.warning(f"공매도 거래량 미확보: {ticker} {date}")
    except Exception as exc:
        result["errors"]["volume"] = str(exc)
        logger.warning(f"공매도 거래량 조회 실패({date}): {exc}")

    if result["shorting_balance"] is not None:
        try:
            from tools.market import _prev_trading_day
            previous = _prev_trading_day(date)
            if previous >= date:
                raise ValueError("직전 거래일이 요청일보다 이르지 않습니다")
            result["prev_balance_date"] = previous
            df_prev = stock.get_shorting_balance_by_date(previous, previous, ticker)
            row = _dated_row(df_prev, previous)
            if row is not None:
                col_qty = _find_col(df_prev, ["공매도잔고", "공매도잔고수량", "잔고수량", "잔고"])
                if col_qty is None:
                    raise ValueError("직전 거래일의 잔고수량 컬럼이 없습니다")
                result["prev_balance"] = _quantity(row[col_qty])
                result["balance_change"] = result["shorting_balance"] - result["prev_balance"]
        except Exception as exc:
            result["errors"]["previous_balance"] = str(exc)
            logger.warning(f"직전 거래일 잔고 조회 실패({date}): {exc}")

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
