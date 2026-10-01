"""Backfill shorting_volume_ratio (당일 공매도거래비중 %) from the already-committed
manual KRX export data/manual_krx/krx_short_402340.xlsx, for factor_log.csv dates
where the column is currently blank. No new manual lookup needed -- the workbook
already has it, just never merged in.

The workbook has ~6x duplicate rows per date (same export run saved repeatedly).
We require every duplicate for a date to agree before using it.

Usage:
  python tools/repair_shorting_volume_ratio.py                 # dry run, prints plan
  python tools/repair_shorting_volume_ratio.py --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
WORKBOOK = BASE / "data/manual_krx/krx_short_402340.xlsx"
COLUMN = "shorting_volume_ratio"
RATIO_COL_INDEX = 8  # 0-based: date,close,chg,pct,vol,amt,short_vol,short_amt,short_ratio


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


def source_values(workbook: Path) -> dict[str, float]:
    wb = openpyxl.load_workbook(workbook, read_only=True)
    ws = wb["Sheet1"]
    by_date: dict[str, set] = {}
    for row in ws.iter_rows(min_row=4, values_only=True):
        date_cell = row[0]
        if not date_cell:
            continue
        key = str(date_cell).replace("/", "")
        by_date.setdefault(key, set()).add(row[RATIO_COL_INDEX])
    values: dict[str, float] = {}
    for date, ratios in by_date.items():
        if len(ratios) != 1:
            raise ValueError(f"Inconsistent duplicate rows for {date}: {ratios}")
        values[date] = float(next(iter(ratios)))
    return values


def plan(factor_path: Path, workbook: Path) -> tuple[bytes, bytes, dict]:
    original = factor_path.read_bytes()
    lines = original.splitlines(keepends=True)
    header = next(csv.reader([lines[0].decode("utf-8-sig").rstrip("\r\n")]))
    col_idx = header.index(COLUMN)
    date_idx = header.index("date")
    source = source_values(workbook)

    patched = []
    filled: dict[str, str] = {}
    for line in lines[1:]:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        body = line[:-len(ending)] if ending else line
        cells = next(csv.reader([body.decode("utf-8-sig")]))
        date = cells[date_idx]
        if cells[col_idx].strip() == "" and date in source:
            cells[col_idx] = f"{source[date]:.2f}"
            filled[date] = cells[col_idx]
            stream = io.StringIO(newline="")
            csv.writer(stream, lineterminator=ending.decode()).writerow(cells)
            line = stream.getvalue().encode("utf-8")
        patched.append(line)
    result = lines[0] + b"".join(patched)
    return original, result, {
        "column": COLUMN, "source": str(workbook), "filled": len(filled), "values": filled,
        "before_sha256": digest(original), "after_sha256": digest(result),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    original, patched, report = plan(args.factor_log, args.workbook)
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
