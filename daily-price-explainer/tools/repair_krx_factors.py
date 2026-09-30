"""Capture KRX daily source rows and fill only historical blank factor cells.

Examples:
  python tools/repair_krx_factors.py sector_semiconductor --capture SNAPSHOT.json
  python tools/repair_krx_factors.py sector_semiconductor --snapshot SNAPSHOT.json
  python tools/repair_krx_factors.py sector_semiconductor --snapshot SNAPSHOT.json --apply --expected-sha256 HASH

The capture contains public market data only, never the KRX authentication key.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
CUTOFF = "20260917"
TICKERS = ("091160", "091230", "396510")
ENDPOINT = {
    "sector_semiconductor": "etp/etf_bydd_trd",
    "kospi_ret": "idx/kospi_dd_trd",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def factor_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields, rows = reader.fieldnames, list(reader)
    if not fields or fields[0] != "date" or len(rows) != len({r["date"] for r in rows}):
        raise ValueError("Unexpected factor log schema or duplicate dates")
    return fields, rows


def missing_dates(rows: list[dict[str, str]], column: str) -> list[str]:
    first = min(row["date"] for row in rows)
    return [row["date"] for row in rows
            if first < row["date"] < CUTOFF and not row[column].strip()]


def capture(column: str, dates: list[str]) -> dict:
    import requests
    import truststore
    from dotenv import dotenv_values

    truststore.inject_into_ssl()
    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    session = requests.Session()
    session.headers["AUTH_KEY"] = key
    result = {}
    for date in dates:
        response = session.get(
            "https://data-dbg.krx.co.kr/svc/apis/" + ENDPOINT[column],
            params={"basDd": date}, timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body.get("OutBlock_1"), list):
            raise ValueError(f"No KRX market rows on {date}: {body.get('respCode')}")
        if column == "sector_semiconductor":
            rows = [r for r in body["OutBlock_1"] if r.get("ISU_CD") in TICKERS]
            if {r["ISU_CD"] for r in rows} != set(TICKERS) or len(rows) != 3:
                raise ValueError(f"Missing or duplicate semiconductor ETF on {date}")
            result[date] = [{k: r[k] for k in
                            ("BAS_DD", "ISU_CD", "ISU_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT")}
                            for r in sorted(rows, key=lambda r: r["ISU_CD"])]
        else:
            rows = [r for r in body["OutBlock_1"]
                    if r.get("IDX_CLSS") == "KOSPI" and r.get("IDX_NM") == "코스피"]
            if len(rows) != 1:
                raise ValueError(f"Missing or duplicate KOSPI main index on {date}")
            result[date] = [{k: rows[0][k] for k in
                            ("BAS_DD", "IDX_CLSS", "IDX_NM", "CLSPRC_IDX", "CMPPREVDD_IDX", "FLUC_RT")}]
    return {"source": "KRX Open API", "endpoint": ENDPOINT[column],
            "column": column, "rows": result}


def values_from_snapshot(snapshot: dict, column: str, dates: list[str]) -> dict[str, str]:
    if snapshot.get("column") != column or snapshot.get("endpoint") != ENDPOINT[column]:
        raise ValueError("Snapshot source does not match requested column")
    if set(snapshot["rows"]) != set(dates):
        raise ValueError("Snapshot dates do not match current missing dates")
    values = {}
    for date in dates:
        rows = snapshot["rows"][date]
        if column == "sector_semiconductor":
            if len(rows) != 3 or {r["ISU_CD"] for r in rows} != set(TICKERS):
                raise ValueError(f"Bad ETF snapshot on {date}")
            prices = [(float(r["TDD_CLSPRC"]), float(r["CMPPREVDD_PRC"])) for r in rows]
            if any(close <= 0 or close - change <= 0 for close, change in prices):
                raise ValueError(f"Bad ETF close on {date}")
            returns = [round(change / (close - change) * 100, 2) for close, change in prices]
            value = round(sum(returns) / 3, 2)
        else:
            if len(rows) != 1 or rows[0]["IDX_NM"] != "코스피":
                raise ValueError(f"Bad KOSPI snapshot on {date}")
            close = float(rows[0]["CLSPRC_IDX"])
            change = float(rows[0]["CMPPREVDD_IDX"])
            if close <= 0 or close - change <= 0:
                raise ValueError(f"Bad KOSPI close on {date}")
            value = round(change / (close - change) * 100, 2)
            if abs(value - float(rows[0]["FLUC_RT"])) > 0.011:
                raise ValueError(f"KRX KOSPI rate mismatch on {date}")
        if any(r["BAS_DD"] != date for r in rows):
            raise ValueError(f"Wrong source date on {date}")
        values[date] = f"{value:.2f}"
    return values


def plan(factor: Path, column: str, snapshot: dict) -> tuple[bytes, bytes, dict]:
    fields, rows = factor_rows(factor)
    index = fields.index(column)
    dates = missing_dates(rows, column)
    values = values_from_snapshot(snapshot, column, dates)
    original = factor.read_bytes()
    lines = original.splitlines(keepends=True)
    if len(lines) != len(rows) + 1:
        raise ValueError("Unexpected physical CSV line count")
    patched = []
    seen = set()
    for line in lines:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        cells = line[:-len(ending)].split(b",") if ending else line.split(b",")
        date = cells[0].decode("ascii")
        if date in values:
            if len(cells) != len(fields) or cells[index] != b"":
                raise ValueError(f"Unexpected CSV row on {date}")
            cells[index] = values[date].encode("ascii")
            seen.add(date)
            line = b",".join(cells) + ending
        patched.append(line)
    if seen != set(values):
        raise ValueError("A planned date was not patched exactly once")
    result = b"".join(patched)
    return original, result, {"column": column, "filled": len(values), "values": values,
                             "before_sha256": digest(original), "after_sha256": digest(result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("column", choices=ENDPOINT)
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--snapshot", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    fields, rows = factor_rows(args.factor_log)
    dates = missing_dates(rows, args.column)
    if args.expected_count is not None and len(dates) != args.expected_count:
        raise ValueError(f"Expected {args.expected_count} gaps, found {len(dates)}")
    if args.capture:
        if args.apply:
            parser.error("--capture cannot be combined with --apply")
        snapshot = capture(args.column, dates)
        atomic_write(args.capture, (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode())
        print(json.dumps({"captured": len(dates), "snapshot": str(args.capture)}))
        return
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    original, patched, report = plan(args.factor_log, args.column, snapshot)
    if args.expected_sha256 and report["before_sha256"] != args.expected_sha256:
        raise ValueError("Factor log changed since expected SHA-256")
    if args.apply:
        if digest(args.factor_log.read_bytes()) != report["before_sha256"]:
            raise ValueError("Factor log changed during repair planning")
        if original != patched:
            atomic_write(args.factor_log, patched)
        if digest(args.factor_log.read_bytes()) != report["after_sha256"]:
            raise ValueError("Write verification failed")
    print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
