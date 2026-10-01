"""Capture KRX cash closes for the ATS-era audit, checkpointed by month.

Run from the project root. Each month is written atomically, so interrupted
captures can resume without repeating successful API requests. Credentials
are read from .env and never saved in the output.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.repair_krx_factors import atomic_write
except ModuleNotFoundError:
    from repair_krx_factors import atomic_write


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
OUTPUT = BASE / "data/snapshots/krx_regular_history"
ENDPOINTS = ("sto/stk_bydd_trd", "sto/ksq_bydd_trd")


def _fetch_rows(session, endpoint: str, date: str) -> list[dict]:
    """Retry transient empty/HTML responses without exposing the API key."""
    import requests

    for attempt in range(5):
        try:
            response = session.get("https://data-dbg.krx.co.kr/svc/apis/" + endpoint,
                                   params={"basDd": date}, timeout=25)
            response.raise_for_status()
            rows = response.json().get("OutBlock_1")
            if isinstance(rows, list) and rows:
                return rows
        except (requests.RequestException, ValueError):
            pass
        if attempt < 4:
            time.sleep(2 ** attempt)
    raise ValueError(f"KRX response unavailable after retries: {endpoint} {date}")


def tickers_for(date: str) -> set[str]:
    tickers = {"402340", "000660", "060570"}
    if date < "20260102":
        tickers.add("216050")  # Incross held until 2026-01-01
    if date >= "20260626":
        tickers.add("205500")  # Nexus newly acquired
    return tickers


def capture(start: str, end: str, out_dir: Path, delay: float = 0.2) -> None:
    import requests
    import truststore
    from dotenv import dotenv_values

    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    truststore.inject_into_ssl()
    with FACTOR.open(encoding="utf-8-sig", newline="") as file:
        dates = [row["date"] for row in csv.DictReader(file)
                 if start <= row["date"] <= end]
    if not dates or len(dates) != len(set(dates)):
        raise ValueError("No unique factor dates in requested range")
    months = {}
    for date in dates:
        months.setdefault(date[:6], []).append(date)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["AUTH_KEY"] = key
    for month, month_dates in months.items():
        path = out_dir / f"krx_regular_{month}.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if sorted(saved.get("rows", {})) != month_dates:
                raise ValueError(f"Existing checkpoint has wrong dates: {path}")
            print(f"skip {month}: {len(month_dates)} dates", flush=True)
            continue
        captured = {}
        for date in month_dates:
            selected = {}
            for endpoint in ENDPOINTS:
                rows = _fetch_rows(session, endpoint, date)
                for row in rows:
                    ticker = row.get("ISU_CD")
                    if ticker in tickers_for(date):
                        if row.get("BAS_DD") != date or ticker in selected:
                            raise ValueError(f"Wrong or duplicate KRX row: {ticker} {date}")
                        selected[ticker] = {key: row[key] for key in
                                            ("BAS_DD", "ISU_CD", "ISU_NM",
                                             "TDD_CLSPRC", "ACC_TRDVOL")}
                time.sleep(delay)
            if set(selected) != tickers_for(date):
                raise ValueError(f"KRX missing tickers on {date}: {tickers_for(date)-set(selected)}")
            captured[date] = selected
        report = {"source": "KRX Open API regular-session equity daily rows",
                  "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                  "endpoints": ENDPOINTS, "date_range": [month_dates[0], month_dates[-1]],
                  "rows": captured}
        atomic_write(path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
        print(f"saved {month}: {len(captured)} dates -> {path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20250304")
    parser.add_argument("--end", default="20260731")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    if args.delay < 0.1:
        parser.error("Delay must be >= 0.1 seconds")
    capture(args.start, args.end, args.output, args.delay)


if __name__ == "__main__":
    main()
