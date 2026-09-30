"""Replace SK Square closes with KRX regular-session closes on selected dates.

This repairs the SK Square numerator and discounts. Listed holdings are a
separate source audit and are deliberately not modified here.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import pandas as pd

try:
    from tools.nav_v2 import compute
    from tools.repair_krx_factors import atomic_write, digest
except ModuleNotFoundError:  # direct execution from tools/
    from nav_v2 import compute
    from repair_krx_factors import atomic_write, digest


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
NAV = BASE / "data/nav_daily.csv"
SOURCE = BASE / "data/snapshots/20260930/krx_regular_closes_aug03_sep29.json"


def patch_rows(data: bytes, updates: dict[str, dict[str, str]]) -> bytes:
    lines = data.splitlines(keepends=True)
    if not lines:
        raise ValueError("Empty CSV")
    header = next(csv.reader([lines[0].decode("utf-8-sig").rstrip("\r\n")]))
    indexes = {column: index for index, column in enumerate(header)}
    result = []
    changed = set()
    for line in lines:
        ending = "\r\n" if line.endswith(b"\r\n") else "\n" if line.endswith(b"\n") else ""
        body = line[:-len(ending)] if ending else line
        cells = next(csv.reader([body.decode("utf-8-sig")]))
        date = cells[0]
        if date not in updates:
            result.append(line)
            continue
        if len(cells) != len(header):
            raise ValueError(f"Unexpected column count on {date}")
        for column, value in updates[date].items():
            cells[indexes[column]] = value
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator=ending).writerow(cells)
        result.append(stream.getvalue().encode("utf-8"))
        changed.add(date)
    if changed != set(updates):
        raise ValueError("Not all selected rows were patched")
    return b"".join(result)


def plan(factor_path: Path, nav_path: Path, snapshot: dict,
         start: str = "20260915", end: str = "20260929") -> tuple[dict[str, bytes], dict]:
    if snapshot.get("source") != "KRX Open API regular-session equity daily rows":
        raise ValueError("Unexpected KRX source")
    factor = pd.read_csv(factor_path, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    nav = pd.read_csv(nav_path, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    dates = sorted(d for d in factor["date"] if start <= d <= end)
    if not dates or len(dates) != len(set(dates)) or not set(dates).issubset(nav["date"]):
        raise ValueError("Target dates missing or duplicated")
    if any(d not in snapshot["rows"] for d in dates):
        raise ValueError("KRX snapshot does not cover all target dates")
    original_factor, original_nav = factor_path.read_bytes(), nav_path.read_bytes()
    nav_updates: dict[str, dict[str, str]] = {}
    for date in dates:
        source = snapshot["rows"][date]["402340"]
        if source["BAS_DD"] != date or source["ISU_CD"] != "402340":
            raise ValueError(f"Wrong KRX row on {date}")
        close = float(source["TDD_CLSPRC"].replace(",", ""))
        if close <= 0:
            raise ValueError(f"Bad KRX close on {date}")
        pos = int(nav.index[nav["date"] == date][0])
        row = nav.iloc[pos]
        shares = float(row["skq_shares"])
        mtm_nps = float(row["nav_mtm"]) / shares
        company_nps = float(row["nav_company"]) / shares
        nav.loc[pos, "skq_close"] = close
        nav.loc[pos, "skq_market_cap"] = close * shares
        nav.loc[pos, "nav_discount_pct_mtm"] = round((1 - close / mtm_nps) * 100, 2)
        nav.loc[pos, "nav_discount_pct_company"] = round((1 - close / company_nps) * 100, 2)
        nav_updates[date] = {
            "skq_close": str(close),
            "skq_market_cap": str(float(nav.loc[pos, "skq_market_cap"])),
            "nav_discount_pct_mtm": str(float(nav.loc[pos, "nav_discount_pct_mtm"])),
            "nav_discount_pct_company": str(float(nav.loc[pos, "nav_discount_pct_company"])),
        }

    ref = nav["date"].map(factor.set_index("date")["skq_ret"])
    calculated = compute(nav, ref)
    calc = calculated.set_index("date")
    factor_updates: dict[str, dict[str, str]] = {}
    for date in dates:
        pos = int(factor.index[factor["date"] == date][0])
        old_implied = float(factor.loc[pos, "nav_implied_ret"])
        new_implied = float(calc.loc[date, "nav_implied_ret"])
        if abs(old_implied - new_implied) > 0.011:
            raise ValueError(f"NAV implied return differs before listed-holdings repair on {date}")
        factor_updates[date] = {
            column: str(float(calc.loc[date, source_col]))
            for column, source_col in (
                ("skq_ret", "skq_ret_v2"),
                ("divergence", "divergence"),
                ("nav_discount_pct", "nav_discount_pct"),
                ("nav_discount_delta", "nav_discount_delta"),
            )
        }
    patched_nav = patch_rows(original_nav, nav_updates)
    patched_factor = patch_rows(original_factor, factor_updates)
    report = {"dates": dates, "factor_updates": factor_updates,
              "nav_updates": nav_updates,
              "factor_before_sha256": digest(original_factor),
              "factor_after_sha256": digest(patched_factor),
              "nav_before_sha256": digest(original_nav),
              "nav_after_sha256": digest(patched_nav)}
    return {"factor_original": original_factor, "factor_patched": patched_factor,
            "nav_original": original_nav, "nav_patched": patched_nav}, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--nav", type=Path, default=NAV)
    parser.add_argument("--snapshot", type=Path, default=SOURCE)
    parser.add_argument("--start", default="20260915")
    parser.add_argument("--end", default="20260929")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-factor-sha256")
    parser.add_argument("--expected-nav-sha256")
    args = parser.parse_args()
    source = json.loads(args.snapshot.read_text(encoding="utf-8"))
    files, report = plan(args.factor_log, args.nav, source, args.start, args.end)
    for name in ("factor", "nav"):
        expected = getattr(args, f"expected_{name}_sha256")
        if expected and expected != report[f"{name}_before_sha256"]:
            raise ValueError(f"{name} changed since expected SHA-256")
    if args.apply:
        if digest(args.factor_log.read_bytes()) != report["factor_before_sha256"] or \
           digest(args.nav.read_bytes()) != report["nav_before_sha256"]:
            raise ValueError("Input changed during planning")
        atomic_write(args.nav, files["nav_patched"])
        try:
            atomic_write(args.factor_log, files["factor_patched"])
        except BaseException:
            atomic_write(args.nav, files["nav_original"])
            raise
        if digest(args.factor_log.read_bytes()) != report["factor_after_sha256"] or \
           digest(args.nav.read_bytes()) != report["nav_after_sha256"]:
            raise ValueError("Written files differ from plan")
    print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
