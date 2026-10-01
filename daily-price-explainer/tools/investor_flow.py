"""KRX-market net shares from Naver PC trend (explicit tradeType=KRX).

foreign_net includes foreign and other-foreign investors. The endpoint's price
fields are not a regular-session close source and must never feed NAV/targets.
"""

from __future__ import annotations

from datetime import datetime


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"Expected signed quantity string: {value!r}")
    return int(value.replace(",", "").replace("+", ""))


def parse_trend_rows(rows: list[dict], ticker: str) -> dict[str, dict]:
    """Parse preserved legacy mobile responses; live collection uses PC below."""
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


def parse_pc_trend_rows(rows: list[dict], ticker: str) -> dict[str, dict]:
    """Normalize a PC trend response captured with an explicit KRX selector."""
    if not isinstance(rows, list):
        raise ValueError("Naver PC trend response is not a list")
    normalized = []
    for row in rows:
        ratio = row.get("frgnHoldRatio")
        if not isinstance(ratio, str) or ratio.endswith("%"):
            raise ValueError("Missing/unexpected PC ownership ratio")
        normalized.append({**row, "foreignerHoldRatio": ratio + "%"})
    return parse_trend_rows(normalized, ticker)


def fetch_trend(ticker: str = "402340") -> dict[str, dict]:
    import requests
    import truststore

    truststore.inject_into_ssl()
    response = requests.get(f"https://stock.naver.com/api/domestic/detail/{ticker}/trend",
                            params={"tradeType": "KRX", "startIdx": 0, "pageSize": 60},
                            headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": f"https://stock.naver.com/domestic/stock/{ticker}/price"},
                            timeout=20)
    response.raise_for_status()
    return parse_pc_trend_rows(response.json(), ticker)


def get_daily_flow(ticker: str, date: str) -> dict:
    """Require an exact finalized trading date; never silently use another day."""
    rows = fetch_trend(ticker)
    if date not in rows:
        raise ValueError(f"Naver trend has no investor quantities for {ticker} {date}")
    return rows[date]
