"""SK Square stock-futures basis from KRX regular-session contracts and spot."""

from __future__ import annotations

import re

from loguru import logger

from tools.krx_regular import market_rows, numeric, stock_close


def _skq_near_month(date: str) -> tuple[dict | None, list[dict]]:
    """Select the highest-volume outright, excluding calendar spreads."""
    raw = [row for row in market_rows(date, "drv/eqsfu_stk_bydd_trd")
           if row.get("PROD_NM") == "SK스퀘어 선물"
           and row.get("BAS_DD") == date
           and re.search(r"\bF\s+\d{6}\b", str(row.get("ISU_NM", "")))]
    contracts = []
    for row in raw:
        if not str(row.get("TDD_CLSPRC", "")).strip():
            continue
        close = numeric(row, "TDD_CLSPRC")
        volume = numeric(row, "ACC_TRDVOL")
        if close <= 0:
            continue
        try:
            change = numeric(row, "CMPPREVDD_PRC")
        except ValueError:
            change = None
        contracts.append({
            "futures_ticker": row["ISU_NM"].strip(),
            "close": close,
            "change": change,
            "volume": volume,
            "spot_close": None,  # never substitute a provider-specific spot field
        })
    active = [contract for contract in contracts if contract["volume"] > 0]
    near = max(active, key=lambda contract: contract["volume"]) if active else None
    return near, contracts


def _get_spot_close(ticker: str, date: str) -> int | None:
    try:
        return stock_close(ticker, date)
    except Exception as exc:
        logger.warning(f"KRX 정규장 현물 종가 조회 실패: {exc}")
        return None


def _basis_pct_on(ticker: str, date: str) -> float | None:
    near, _ = _skq_near_month(date)
    if not near:
        return None
    spot = _get_spot_close(ticker, date)
    return round((near["close"] - spot) / spot * 100, 3) if spot else None


def get_futures_data(ticker: str, date: str) -> dict:
    """KRX outright futures and KRX cash close for the exact trading date."""
    try:
        near, contracts = _skq_near_month(date)
        if near is None:
            return {"date": date, "ticker": ticker, "listed": False,
                    "error": "KRX SK스퀘어 주식선물 체결 없음"}
        spot = stock_close(ticker, date)
        basis = near["close"] - spot
        basis_pct = round(basis / spot * 100, 3)
        fut_prev = near["close"] - near["change"] if near["change"] is not None else None
        fut_pct = round(near["change"] / fut_prev * 100, 2) if fut_prev else None
        from tools.market import _prev_trading_day

        previous = _prev_trading_day(date)
        try:
            prev_basis_pct = _basis_pct_on(ticker, previous)
        except Exception as exc:
            logger.warning(f"전일 선물 베이시스 조회 실패: {exc}")
            prev_basis_pct = None
        basis_change_pct = (round(basis_pct - prev_basis_pct, 3)
                            if prev_basis_pct is not None else None)
        return {"date": date, "ticker": ticker, "listed": True,
                "near_month": near, "spot_close": spot,
                "basis": basis, "basis_pct": basis_pct,
                "fut_pct": fut_pct, "prev_basis_pct": prev_basis_pct,
                "basis_change_pct": basis_change_pct,
                "open_interest_total": None, "oi_change": None,
                "contracts": contracts}
    except Exception as exc:
        logger.error(f"get_futures_data error: {exc}")
        return {"date": date, "ticker": ticker, "listed": False,
                "error": str(exc)}
