"""Exact-date KRX index/ETF returns and Bank of Korea USD/KRW fixing."""

from __future__ import annotations

import math

try:
    from tools.krx_regular import BASE, market_rows
except ModuleNotFoundError:
    from krx_regular import BASE, market_rows

SEMICONDUCTOR_ETFS = ("091160", "091230", "396510")


def _rate(row: dict, close_field: str, change_field: str) -> float:
    close = float(str(row[close_field]).replace(",", ""))
    change = float(str(row[change_field]).replace(",", ""))
    if not math.isfinite(close) or not math.isfinite(change) or close <= 0 or close - change <= 0:
        raise ValueError("Invalid KRX close/change")
    rate = round(change / (close - change) * 100, 2)
    if abs(rate - float(row["FLUC_RT"])) > 0.011:
        raise ValueError("KRX reported return differs from close/change")
    return rate


def krx_market_factors(date: str) -> tuple[dict, dict]:
    """Return factor values and the exact source rows, without writing data."""
    indexes = [row for row in market_rows(date, "idx/kospi_dd_trd")
               if row.get("IDX_CLSS") == "KOSPI" and row.get("IDX_NM") == "코스피"]
    if len(indexes) != 1 or indexes[0].get("BAS_DD") != date:
        raise ValueError(f"KRX KOSPI index missing/duplicated on {date}")
    kospi = _rate(indexes[0], "CLSPRC_IDX", "CMPPREVDD_IDX")

    etfs = [row for row in market_rows(date, "etp/etf_bydd_trd")
            if row.get("ISU_CD") in SEMICONDUCTOR_ETFS]
    if (len(etfs) != 3 or {row["ISU_CD"] for row in etfs} != set(SEMICONDUCTOR_ETFS)
            or any(row.get("BAS_DD") != date for row in etfs)):
        raise ValueError(f"KRX semiconductor ETFs missing/duplicated on {date}")
    sector = round(sum(_rate(row, "TDD_CLSPRC", "CMPPREVDD_PRC")
                       for row in etfs) / 3, 2)
    return ({"kospi_ret": kospi, "sector_semiconductor": sector},
            {"kospi": indexes[0], "semiconductor_etfs": etfs})


def bok_usd_krw(date: str) -> tuple[float, dict]:
    """Read ECOS 731Y001/0000001 for the exact requested date."""
    import requests
    import truststore
    from dotenv import dotenv_values

    key = dotenv_values(BASE / ".env").get("BOK_API_KEY")
    if not key:
        raise ValueError("BOK_API_KEY is absent")
    truststore.inject_into_ssl()
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/5/"
           f"731Y001/D/{date}/{date}/0000001")
    try:
        response = requests.get(url, timeout=15)
    except requests.RequestException:
        raise ValueError(f"ECOS request failed on {date}") from None
    if response.status_code != 200:
        raise ValueError(f"ECOS HTTP {response.status_code} on {date}")
    rows = response.json().get("StatisticSearch", {}).get("row", [])
    rows = [row for row in rows
            if row.get("TIME") == date and row.get("STAT_CODE") == "731Y001"
            and row.get("ITEM_CODE1") == "0000001"]
    if len(rows) != 1:
        raise ValueError(f"ECOS USD/KRW fixing missing/duplicated on {date}")
    value = float(str(rows[0]["DATA_VALUE"]).replace(",", ""))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"ECOS USD/KRW fixing invalid on {date}")
    return value, rows[0]


def get_daily_macro(date: str) -> dict:
    factors, _ = krx_market_factors(date)
    usd, _ = bok_usd_krw(date)
    return {**factors, "usd_krw": usd}
