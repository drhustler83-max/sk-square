"""Backfill shorting_balance_change where both the day's and the prior day's
shorting_balance are already present in factor_log.csv (pure local recompute,
no external source needed). Dates where the prior day's balance is itself
missing are left untouched -- those need a manual KRX value first.

Usage:
  python tools/repair_shorting_balance_change.py                      # dry run, prints plan
  python tools/repair_shorting_balance_change.py --apply              # apply
  python tools/repair_shorting_balance_change.py --apply --expected-sha256 HASH
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

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
COLUMN = "shorting_balance_change"
SOURCE_COLUMN = "shorting_balance"


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


def plan(factor_path: Path) -> tuple[bytes, bytes, dict]:
    original = factor_path.read_bytes()
    df = pd.read_csv(factor_path, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    if df["date"].duplicated().any():
        raise ValueError("Duplicate dates in factor_log.csv")

    values: dict[str, str] = {}
    for i in range(1, len(df)):
        cur_change = df.loc[i, COLUMN]
        if pd.notna(cur_change) and str(cur_change).strip() != "":
            continue
        cur_bal = df.loc[i, SOURCE_COLUMN]
        prev_bal = df.loc[i - 1, SOURCE_COLUMN]
        if pd.isna(cur_bal) or pd.isna(prev_bal):
            continue
        change = float(cur_bal) - float(prev_bal)
        if change != int(change):
            raise ValueError(f"Non-integer shorting_balance diff on {df.loc[i, 'date']}")
        values[df.loc[i, "date"]] = str(int(change))

    lines = original.splitlines(keepends=True)
    header = next(csv.reader([lines[0].decode("utf-8-sig").rstrip("\r\n")]))
    col_idx = header.index(COLUMN)
    patched = []
    seen = set()
    for line in lines:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        body = line[:-len(ending)] if ending else line
        cells = next(csv.reader([body.decode("utf-8-sig")]))
        date = cells[0]
        if date in values:
            if cells[col_idx].strip() != "":
                raise ValueError(f"Expected blank {COLUMN} on {date}")
            cells[col_idx] = values[date]
            stream = io.StringIO(newline="")
            csv.writer(stream, lineterminator=ending.decode()).writerow(cells)
            line = stream.getvalue().encode("utf-8")
            seen.add(date)
        patched.append(line)
    if seen != set(values):
        raise ValueError("A planned date was not patched exactly once")
    result = b"".join(patched)
    return original, result, {
        "column": COLUMN, "filled": len(values), "values": values,
        "before_sha256": digest(original), "after_sha256": digest(result),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    original, patched, report = plan(args.factor_log)
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
