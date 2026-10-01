"""Compare saved NAV inputs with captured KRX regular-session cash closes."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

try:
    from tools.capture_krx_ats_history import BASE, FACTOR, OUTPUT
    from tools.krx_regular import numeric
    from tools.repair_krx_factors import atomic_write
except ModuleNotFoundError:
    from capture_krx_ats_history import BASE, FACTOR, OUTPUT
    from krx_regular import numeric
    from repair_krx_factors import atomic_write


RECENT = BASE / "data/snapshots/20260930/krx_regular_closes_aug03_sep29.json"
NAV = BASE / "data/nav_daily.csv"
LISTED = BASE / "data/listed_holdings_daily.csv"
HELD = {"000660": "skhynix", "060570": "dreamus",
        "216050": "incross", "205500": "nexus"}


def csv_rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    dates = [row["date"] for row in rows]
    if len(dates) != len(set(dates)):
        raise ValueError(f"Duplicate dates in {path}")
    return {row["date"]: row for row in rows}


def capture_rows() -> dict[str, dict]:
    rows = {}
    for path in sorted(OUTPUT.glob("krx_regular_*.json")) + [RECENT]:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("source") != "KRX Open API regular-session equity daily rows":
            raise ValueError(f"Unexpected source in {path}")
        for date, day in report["rows"].items():
            if date in rows:
                raise ValueError(f"Duplicate capture date {date}: {path}")
            rows[date] = day
    return rows


def audit(start: str = "20250304", end: str = "20260929") -> dict:
    factor = csv_rows(FACTOR)
    nav = csv_rows(NAV)
    listed = csv_rows(LISTED)
    captured = capture_rows()
    expected = sorted(date for date in factor if start <= date <= end)
    missing_dates = [date for date in expected if date not in captured]
    mismatches = []
    checked = Counter()
    no_trade = Counter()
    for date in expected:
        day = captured.get(date)
        if day is None:
            continue
        requested = {"402340": (nav, "skq_close")}
        requested.update({ticker: (listed, name + "_price")
                          for ticker, name in HELD.items()
                          if int(float(listed[date][name + "_shares"])) > 0})
        for ticker, (table, column) in requested.items():
            row = day.get(ticker)
            if row is None:
                raise ValueError(f"No captured row for held ticker {ticker} on {date}")
            if row["BAS_DD"] != date or row["ISU_CD"] != ticker:
                raise ValueError(f"Wrong KRX row for {ticker} on {date}")
            if numeric(row, "ACC_TRDVOL") <= 0:
                no_trade[ticker] += 1
                continue
            krx = numeric(row, "TDD_CLSPRC")
            saved = float(table[date][column])
            checked[ticker] += 1
            if abs(krx - saved) > 0.5:
                mismatches.append({"date": date, "ticker": ticker,
                                   "column": column, "saved": saved, "krx": krx})
    return {"expected_dates": len(expected), "captured_dates": len(expected) - len(missing_dates),
            "missing_dates": missing_dates, "checked": dict(checked),
            "no_trade": dict(no_trade), "mismatches": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20250304")
    parser.add_argument("--end", default="20260929")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.start, args.end)
    if args.report:
        atomic_write(args.report, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and result["missing_dates"]:
        raise SystemExit("KRX history capture is incomplete")


if __name__ == "__main__":
    main()
