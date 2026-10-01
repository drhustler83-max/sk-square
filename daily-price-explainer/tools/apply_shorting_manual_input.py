"""Merge Sean's manually-collected KRX shorting_balance / shorting_balance_change
values (data/manual_krx/shorting_gap_input_402340.xlsx) into factor_log.csv.

Convention (confirmed with Sean 2026-10-01): values are stored under their TRUE
calendar date (no T+1 lag carried over from the legacy tools/short.py collector).
shorting_balance_ratio is auto-computed here from shorting_balance / skq_shares
(data/nav_daily.csv), never entered manually.

Usage:
  python tools/apply_shorting_manual_input.py                 # dry run
  python tools/apply_shorting_manual_input.py --apply
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
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
NAV = BASE / "data/nav_daily.csv"
WORKBOOK = BASE / "data/manual_krx/shorting_gap_input_402340.xlsx"

# Dates where the legacy tools/short.py T+1-lag collector already wrote a value
# under this date, but it's a stale duplicate of the TRUE PRIOR date's balance
# (confirmed 2026-10-01: e.g. old row 20220204 == Sean's true 20220203 value).
# For these dates only, overwrite shorting_balance/_ratio/_change with the fresh
# true-date reading instead of refusing on "already populated".
KNOWN_LAG_DUPLICATE_DATES = {
    "20220204", "20231005", "20240920", "20250203", "20251013", "20260220",
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


def read_manual_input(workbook: Path) -> dict[str, dict[str, float]]:
    wb = openpyxl.load_workbook(workbook, data_only=True)
    ws = wb["공매도 입력"]
    out: dict[str, dict[str, float]] = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        date_cell, balance, _ratio, vol_ratio, change, _note = row
        if not date_cell or not str(date_cell).startswith("20"):
            continue
        date = str(date_cell).replace("-", "")
        entry: dict[str, float] = {}
        if isinstance(balance, (int, float)):
            entry["shorting_balance"] = float(balance)
        if isinstance(change, (int, float)):
            entry["shorting_balance_change"] = float(change)
        if isinstance(vol_ratio, (int, float)):
            entry["shorting_volume_ratio"] = float(vol_ratio)
        if entry:
            out[date] = entry
    return out


def plan(factor_path: Path, nav_path: Path, workbook: Path) -> tuple[bytes, bytes, dict]:
    original = factor_path.read_bytes()
    manual = read_manual_input(workbook)
    if not manual:
        raise ValueError("No manual values found in workbook")

    shares = pd.read_csv(nav_path, dtype={"date": str}).set_index("date")["skq_shares"]

    lines = original.splitlines(keepends=True)
    header = next(csv.reader([lines[0].decode("utf-8-sig").rstrip("\r\n")]))
    col_idx = {c: header.index(c) for c in
               ("shorting_balance", "shorting_balance_ratio", "shorting_volume_ratio", "shorting_balance_change")}
    date_idx = header.index("date")

    applied: dict[str, dict[str, str]] = {}
    patched = [lines[0]]
    for line in lines[1:]:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        body = line[:-len(ending)] if ending else line
        cells = next(csv.reader([body.decode("utf-8-sig")]))
        date = cells[date_idx]
        entry = manual.get(date)
        if entry:
            overwrite_ok = date in KNOWN_LAG_DUPLICATE_DATES
            changes: dict[str, str] = {}
            if "shorting_balance" in entry:
                prior = cells[col_idx["shorting_balance"]].strip()
                if prior != "" and not overwrite_ok:
                    raise ValueError(f"shorting_balance already populated on {date}")
                bal = entry["shorting_balance"]
                if prior != "":
                    changes["shorting_balance_replaced_legacy_value"] = prior
                cells[col_idx["shorting_balance"]] = str(int(bal))
                changes["shorting_balance"] = str(int(bal))
                if date not in shares.index or pd.isna(shares[date]):
                    raise ValueError(f"No skq_shares for {date} to compute ratio")
                prior_ratio = cells[col_idx["shorting_balance_ratio"]].strip()
                if prior_ratio != "" and not overwrite_ok:
                    raise ValueError(f"shorting_balance_ratio already populated on {date}")
                ratio = round(bal / float(shares[date]) * 100, 2)
                if prior_ratio != "":
                    changes["shorting_balance_ratio_replaced_legacy_value"] = prior_ratio
                cells[col_idx["shorting_balance_ratio"]] = f"{ratio:.2f}"
                changes["shorting_balance_ratio"] = f"{ratio:.2f}"
            if "shorting_balance_change" in entry:
                prior_chg = cells[col_idx["shorting_balance_change"]].strip()
                if prior_chg != "" and not overwrite_ok:
                    raise ValueError(f"shorting_balance_change already populated on {date}")
                chg = entry["shorting_balance_change"]
                if prior_chg != "":
                    changes["shorting_balance_change_replaced_legacy_value"] = prior_chg
                cells[col_idx["shorting_balance_change"]] = str(int(chg))
                changes["shorting_balance_change"] = str(int(chg))
            if "shorting_volume_ratio" in entry:
                if cells[col_idx["shorting_volume_ratio"]].strip() != "":
                    raise ValueError(f"shorting_volume_ratio already populated on {date}")
                vr = entry["shorting_volume_ratio"]
                cells[col_idx["shorting_volume_ratio"]] = f"{vr:.2f}"
                changes["shorting_volume_ratio"] = f"{vr:.2f}"
            if changes:
                applied[date] = changes
                stream = io.StringIO(newline="")
                csv.writer(stream, lineterminator=ending.decode()).writerow(cells)
                line = stream.getvalue().encode("utf-8")
        patched.append(line)

    result = b"".join(patched)
    return original, result, {
        "source": str(workbook), "applied_dates": len(applied), "applied": applied,
        "before_sha256": digest(original), "after_sha256": digest(result),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--nav", type=Path, default=NAV)
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    original, patched, report = plan(args.factor_log, args.nav, args.workbook)
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
