"""SK Square investor net purchases in shares, sourced from Naver trend."""

from __future__ import annotations

from datetime import datetime


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"Expected signed quantity string: {value!r}")
    return int(value.replace(",", "").replace("+", ""))


def parse_trend_rows(rows: list[dict], ticker: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError("Naver trend response is not a list")
    result = {}
    for row in rows:
        if row.get("itemCode") != ticker:
            raise ValueError("Naver trend ticker mismatch")
        date = row.get("bizdate")
        if not isinstance(date, str) or len(date) != 8 or not date.isdecimal() or date in result:
            raise ValueError(f"Bad or duplicate Naver trend date: {date!r}")
        datetime.strptime(date, "%Y%m%d")
        pct = row.get("foreignerHoldRatio")
        if not isinstance(pct, str) or not pct.endswith("%"):
            raise ValueError(f"Missing Naver ownership ratio: {date}")
        ratio = float(pct[:-1].replace(",", ""))
        if not 0 <= ratio <= 100:
            raise ValueError(f"Out-of-range Naver ownership ratio: {date}")
        result[date] = {
            "foreign_net": _integer(row["foreignerPureBuyQuant"]),
            "institution_net": _integer(row["organPureBuyQuant"]),
            "individual_net": _integer(row["individualPureBuyQuant"]),
            "foreign_own_pct": ratio,
        }
    return result


def fetch_trend(ticker: str = "402340") -> dict[str, dict]:
    import requests
    import truststore

    truststore.inject_into_ssl()
    response = requests.get(f"https://m.stock.naver.com/api/stock/{ticker}/trend",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    response.raise_for_status()
    return parse_trend_rows(response.json(), ticker)


def get_daily_flow(ticker: str, date: str) -> dict:
    """Require an exact finalized trading date; never silently use another day."""
    rows = fetch_trend(ticker)
    if date not in rows:
        raise ValueError(f"Naver trend has no investor quantities for {ticker} {date}")
    return rows[date]
