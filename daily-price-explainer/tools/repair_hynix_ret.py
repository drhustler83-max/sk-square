"""Fill historical missing Hynix returns from saved daily Hynix closes.

Only empty hynix_ret cells before 2026-09-17 are eligible. Existing values,
including any discrepancies, remain unchanged and are reported separately.
The CSV is patched byte-for-byte outside the eligible cells.

Usage:
    python tools/repair_hynix_ret.py
    python tools/repair_hynix_ret.py --apply --expected-sha256 HASH
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
DEFAULT_FACTOR = BASE / "data" / "factor_log.csv"
DEFAULT_LISTED = BASE / "data" / "listed_holdings_daily.csv"
RECENT_CUTOFF = "20260917"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan(factor_path: Path, listed_path: Path) -> tuple[bytes, bytes, dict]:
    with listed_path.open(encoding="utf-8-sig", newline="") as file:
        listed = sorted(csv.DictReader(file), key=lambda row: row["date"])
    dates = [row["date"] for row in listed]
    if len(dates) != len(set(dates)):
        raise ValueError("Duplicate dates in listed holdings")

    returns: dict[str, float] = {}
    for previous, current in zip(listed, listed[1:]):
        before = float(previous["skhynix_price"])
        after = float(current["skhynix_price"])
        if not all(math.isfinite(price) and price > 0 for price in (before, after)):
            continue
        returns[current["date"]] = round((after / before - 1) * 100, 2)

    with factor_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or reader.fieldnames[:3] != ["date", "skq_ret", "hynix_ret"]:
            raise ValueError("Unexpected factor_log header")
        factor_rows = list(reader)
    factor_dates = [row["date"] for row in factor_rows]
    if len(factor_dates) != len(set(factor_dates)):
        raise ValueError("Duplicate dates in factor log")

    updates: dict[str, str] = {}
    existing_discrepancies: list[dict] = []
    for row in factor_rows:
        date = row["date"]
        if date >= RECENT_CUTOFF or date not in returns:
            continue
        expected = returns[date]
        if not row["hynix_ret"].strip():
            updates[date] = str(expected)
        elif abs(float(row["hynix_ret"]) - expected) > 0.011:
            existing_discrepancies.append({
                "date": date,
                "stored": row["hynix_ret"],
                "from_listed_close": expected,
            })

    original = factor_path.read_bytes()
    lines = original.splitlines(keepends=True)
    if len(lines) != len(factor_rows) + 1:
        raise ValueError("Unexpected physical CSV line count")
    patched = []
    changed_dates = set()
    for line in lines:
        if line.endswith(b"\r\n"):
            body, ending = line[:-2], b"\r\n"
        elif line.endswith(b"\n"):
            body, ending = line[:-1], b"\n"
        else:
            body, ending = line, b""
        fields = body.split(b",", 3)
        date = fields[0].decode("ascii")
        if date in updates:
            if len(fields) != 4 or fields[2] != b"":
                raise ValueError(f"Expected empty third CSV cell on {date}")
            fields[2] = updates[date].encode("ascii")
            line = b",".join(fields) + ending
            changed_dates.add(date)
        patched.append(line)
    if changed_dates != set(updates):
        raise ValueError("Not every planned date was patched exactly once")
    result = b"".join(patched)
    report = {
        "source": str(listed_path),
        "factor": str(factor_path),
        "historical_missing_filled": len(updates),
        "updates": updates,
        "existing_discrepancies_not_modified": existing_discrepancies,
        "before_sha256": sha256(original),
        "after_sha256": sha256(result),
    }
    return original, result, report


def atomic_replace(path: Path, data: bytes) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        Path(temp_path).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=DEFAULT_FACTOR)
    parser.add_argument("--listed", type=Path, default=DEFAULT_LISTED)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    original, patched, report = plan(args.factor_log, args.listed)
    if args.expected_sha256 and report["before_sha256"] != args.expected_sha256:
        raise ValueError("Factor log changed since the expected SHA-256 was recorded")
    if args.apply:
        if sha256(args.factor_log.read_bytes()) != report["before_sha256"]:
            raise ValueError("Factor log changed during repair planning")
        if original != patched:
            atomic_replace(args.factor_log, patched)
        if sha256(args.factor_log.read_bytes()) != report["after_sha256"]:
            raise ValueError("Written factor log does not match planned SHA-256")
    print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
