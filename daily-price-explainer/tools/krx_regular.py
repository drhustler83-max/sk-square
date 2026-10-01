"""KRX regular-session stock rows shared by NAV, logger, and chatbot.

The preserved Aug/Sep 2026 capture is read locally. New dates are fetched
from KRX Open API with an authenticated key that is never included in output.
No pykrx/NXT close or prior-day fallback is used for a requested date.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
SNAPSHOT = BASE / "data/snapshots/20260930/krx_regular_closes_aug03_sep29.json"
KOSDAQ = {"060570", "216050", "039860", "205500"}


class NoRegularTrade(ValueError):
    """The requested ticker has no executed regular-session trade on date."""


def endpoint_for(ticker: str) -> str:
    return "sto/ksq_bydd_trd" if ticker in KOSDAQ else "sto/stk_bydd_trd"


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    if not SNAPSHOT.exists():
        return {}
    report = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if report.get("source") != "KRX Open API regular-session equity daily rows":
        raise ValueError("Unexpected regular-close snapshot")
    return report["rows"]


def market_rows(date: str, endpoint: str) -> list[dict]:
    """Fetch one KRX market board on an exact date. Never cache credentials."""
    import requests
    import truststore
    from dotenv import dotenv_values

    if endpoint not in ("sto/stk_bydd_trd", "sto/ksq_bydd_trd",
                        "drv/eqsfu_stk_bydd_trd", "idx/kospi_dd_trd",
                        "etp/etf_bydd_trd"):
        raise ValueError("Unsupported KRX endpoint")
    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    truststore.inject_into_ssl()
    response = requests.get("https://data-dbg.krx.co.kr/svc/apis/" + endpoint,
                            params={"basDd": date}, headers={"AUTH_KEY": key}, timeout=20)
    response.raise_for_status()
    rows = response.json().get("OutBlock_1")
    if not isinstance(rows, list):
        raise ValueError(f"KRX returned no rows for {endpoint} on {date}")
    return rows


def stock_row(ticker: str, date: str) -> dict:
    saved = _snapshot().get(date, {}).get(ticker)
    if saved is not None:
        row = saved
    else:
        found = [row for row in market_rows(date, endpoint_for(ticker))
                 if row.get("ISU_CD") == ticker]
        if len(found) != 1:
            raise NoRegularTrade(f"KRX has no unique {ticker} regular-session row on {date}")
        row = found[0]
    if row.get("BAS_DD") != date or row.get("ISU_CD") != ticker:
        raise ValueError(f"KRX row date/ticker mismatch: {ticker} {date}")
    return row


def numeric(row: dict, field: str) -> int:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"KRX {field} missing")
    parsed = float(str(value).replace(",", ""))
    if int(parsed) != parsed:
        raise ValueError(f"KRX {field} is not integral: {value!r}")
    return int(parsed)


def stock_close(ticker: str, date: str) -> int:
    row = stock_row(ticker, date)
    if numeric(row, "ACC_TRDVOL") <= 0:
        raise NoRegularTrade(f"KRX {ticker} has no executed regular-session trade on {date}")
    close = numeric(row, "TDD_CLSPRC")
    if close <= 0:
        raise ValueError(f"KRX {ticker} has invalid close on {date}")
    return close


def overlay_closes(series, ticker: str, start: str = "20260803",
                   *, allow_halt: bool = False):
    """Replace a pandas date-indexed price series from `start` with KRX closes."""
    out = series.copy()
    for day in out.index:
        date = day.strftime("%Y%m%d")
        if date < start:
            continue
        try:
            out.loc[day] = stock_close(ticker, date)
        except NoRegularTrade:
            if not (allow_halt and ticker == "060570" and
                    "20260731" <= date <= "20260824"):
                raise
    return out
