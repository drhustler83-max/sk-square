"""
Futures Tool
pykrx를 통해 SK스퀘어(402340) 주식선물 베이시스·만기별 데이터 수집

API: stock.get_future_ohlcv_by_ticker(date, "KRDRVFUEQU")
     → 전 종목 단일주식선물 OHLCV, 컬럼: 종목명·종가·대비·시가·고가·저가·현물가·거래량·거래대금
     SK스퀘어 계약 필터: 종목명.str.contains("스퀘어")
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

# KRX 단일주식선물 상품코드
_FUTURE_PRODUCT = "KRDRVFUEQU"


def _skq_near_month(date: str):
    """해당일 SK스퀘어 단일주식선물 최근월물(거래량 최대) 계약 + 전체 계약 리스트 반환.

    Returns: (near_month_dict | None, contracts_list)
    """
    df = stock.get_future_ohlcv_by_ticker(date, _FUTURE_PRODUCT)
    if df is None or df.empty:
        return None, []

    mask = df.index.astype(str).str.contains("스퀘어", na=False)
    if not mask.any() and "종목명" in df.columns:
        mask = df["종목명"].astype(str).str.contains("스퀘어", na=False)
    sksq = df[mask].copy()
    if sksq.empty:
        return None, []

    def col(row, candidates):
        for c in candidates:
            if c in row.index:
                try:
                    v = row[c]
                    if v is not None:
                        return v
                except Exception:
                    pass
        return None

    def safe_int(v):
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    contracts = []
    for idx, row in sksq.iterrows():
        name = str(idx) if "종목명" not in row.index else str(row.get("종목명", idx))
        contracts.append({
            "futures_ticker": name,
            "close":          safe_int(col(row, ["종가", "Close"])),
            "change":         safe_int(col(row, ["대비", "Change"])),
            "volume":         safe_int(col(row, ["거래량", "Volume"])),
            "spot_close":     safe_int(col(row, ["현물가", "Spot"])),
        })

    active = [c for c in contracts if c["volume"] and c["volume"] > 0] or contracts
    near = max(active, key=lambda x: x["volume"] or 0)
    return near, contracts


def _basis_pct_on(ticker: str, date: str):
    """해당일 SK스퀘어 최근월물 베이시스율(%) — 전일 대비 변화 계산용. 없으면 None."""
    near, _ = _skq_near_month(date)
    if not near or not near.get("close"):
        return None
    spot = _get_spot_close(ticker, date) or near.get("spot_close")
    if not spot:
        return None
    return round((near["close"] - spot) / spot * 100, 3)


def get_futures_data(ticker: str, date: str) -> dict:
    """
    주식선물 베이시스·만기별 데이터 수집

    Args:
        ticker: 기초자산 종목코드 (e.g. '402340')  — 현재는 이름 기반 필터링 사용
        date:   'YYYYMMDD'

    Returns:
        listed (bool):               KRX 주식선물 계약 존재 여부
        near_month (dict):           최근월물 정보 (거래량 최대 계약)
          ├ futures_ticker (str):    선물 종목명
          ├ close (int):             선물 종가
          ├ change (int):            전일 대비
          ├ volume (int):            거래량
          └ spot_close (int):        현물가 (해당 계약의 현물가 필드)
        spot_close (int):            현물 종가 (pykrx 직접 조회)
        basis (int):                 선물종가 - 현물종가  (양수=콘탱고, 음수=백워데이션)
        basis_pct (float):           basis / 현물종가 × 100
        open_interest_total (None):  OI 미수집 (API 미지원)
        oi_change (None):            OI 변화 미수집
        contracts (list[dict]):      만기별 상세
    """
    try:
        near, contracts = _skq_near_month(date)
        if near is None:
            logger.info(f"SK스퀘어 선물 계약 없음/데이터 없음: {date}")
            return {"date": date, "ticker": ticker, "listed": False,
                    "error": "SK스퀘어 주식선물 미상장 또는 데이터 없음 (휴장/만기 후)"}

        logger.info(f"SK스퀘어 선물 계약 {len(contracts)}개 조회: "
                    f"{[c['futures_ticker'] for c in contracts]}")

        # 현물 종가 (현물가 필드가 있으면 우선 사용)
        spot = _get_spot_close(ticker, date)
        if spot is None and near.get("spot_close"):
            spot = near["spot_close"]

        # 베이시스 (선물 - 현물; 양수=콘탱고, 음수=백워데이션)
        basis = basis_pct = None
        if spot and near.get("close"):
            basis = near["close"] - spot
            basis_pct = round(basis / spot * 100, 3)

        # 최근월물 선물 등락률 (현물 등락률과 선행/후행 비교용)
        fut_pct = None
        if near.get("close") is not None and near.get("change") is not None:
            fut_prev = near["close"] - near["change"]
            if fut_prev:
                fut_pct = round(near["change"] / fut_prev * 100, 2)

        # 전일 대비 베이시스 변화 (premium 확대/축소 판정용)
        from tools.market import _prev_trading_day
        prev_basis_pct = _basis_pct_on(ticker, _prev_trading_day(date))
        basis_change_pct = (round(basis_pct - prev_basis_pct, 3)
                            if basis_pct is not None and prev_basis_pct is not None else None)

        logger.info(
            f"[선물] 최근월물={near['futures_ticker']} 종가={near.get('close')} "
            f"현물={spot} 베이시스={basis} (전일대비Δ={basis_change_pct})"
        )

        return {
            "date":                date,
            "ticker":              ticker,
            "listed":              True,
            "near_month":          near,
            "spot_close":          spot,
            "basis":               basis,
            "basis_pct":           basis_pct,
            "fut_pct":             fut_pct,            # 최근월물 선물 등락률(%)
            "prev_basis_pct":      prev_basis_pct,     # 전일 베이시스율(%)
            "basis_change_pct":    basis_change_pct,   # 전일 대비 베이시스 변화(%p, +확대/−축소)
            "open_interest_total": None,   # API 미지원
            "oi_change":           None,
            "contracts":           contracts,
        }

    except Exception as e:
        logger.error(f"get_futures_data error: {e}")
        return {"date": date, "ticker": ticker, "listed": False, "error": str(e)}


def _get_spot_close(ticker: str, date: str):
    """현물 종가 조회"""
    try:
        df = stock.get_market_ohlcv(date, date, ticker)
        if not df.empty:
            return int(df.iloc[0]["종가"])
    except Exception as e:
        logger.warning(f"현물 종가 조회 실패: {e}")
    return None
