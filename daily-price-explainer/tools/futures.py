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
        # ── 1. 단일주식선물 전체 조회 ─────────────────────────
        df = stock.get_future_ohlcv_by_ticker(date, _FUTURE_PRODUCT)

        if df is None or df.empty:
            logger.info(f"주식선물 데이터 없음 (휴장일 가능): {date}")
            return {"date": date, "ticker": ticker, "listed": False,
                    "error": "주식선물 데이터 없음 (휴장일 또는 API 오류)"}

        # ── 2. SK스퀘어 계약 필터링 ──────────────────────────
        mask = df.index.astype(str).str.contains("스퀘어", na=False)
        # 일부 버전에서는 인덱스가 종목명인 경우도 있음
        if not mask.any() and "종목명" in df.columns:
            mask = df["종목명"].astype(str).str.contains("스퀘어", na=False)

        sksq = df[mask].copy()

        if sksq.empty:
            logger.info(f"SK스퀘어 선물 계약 없음: {date}")
            return {"date": date, "ticker": ticker, "listed": False,
                    "error": "SK스퀘어 주식선물 계약이 조회되지 않음 (미상장 또는 만기 후)"}

        logger.info(f"SK스퀘어 선물 계약 {len(sksq)}개 조회: {sksq.index.tolist()}")

        # ── 3. 컬럼 매핑 (pykrx 버전 허용) ──────────────────
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
            close_v = col(row, ["종가", "Close"])
            change_v = col(row, ["대비", "Change"])
            vol_v = col(row, ["거래량", "Volume"])
            spot_v = col(row, ["현물가", "Spot"])
            contracts.append({
                "futures_ticker": name,
                "close":          safe_int(close_v),
                "change":         safe_int(change_v),
                "volume":         safe_int(vol_v),
                "spot_close":     safe_int(spot_v),
            })

        # ── 4. 최근월물: 거래량 최대 계약 ────────────────────
        active = [c for c in contracts if c["volume"] and c["volume"] > 0]
        if not active:
            active = contracts  # 모두 거래량 0이면 전부 후보
        near = max(active, key=lambda x: x["volume"] or 0)

        # ── 5. 현물 종가 (pykrx market 직접 조회) ────────────
        spot = _get_spot_close(ticker, date)
        # 현물가 필드가 있으면 우선 사용
        if spot is None and near.get("spot_close"):
            spot = near["spot_close"]

        # ── 6. 베이시스 계산 ──────────────────────────────────
        basis = None
        basis_pct = None
        if spot and near.get("close"):
            basis = near["close"] - spot
            basis_pct = round(basis / spot * 100, 3)

        logger.info(
            f"[선물] 최근월물={near['futures_ticker']} 종가={near.get('close')} "
            f"현물={spot} 베이시스={basis}"
        )

        return {
            "date":                date,
            "ticker":              ticker,
            "listed":              True,
            "near_month":          near,
            "spot_close":          spot,
            "basis":               basis,
            "basis_pct":           basis_pct,
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
