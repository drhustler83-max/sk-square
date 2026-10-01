"""Legacy HTS quantity parser and historical migration audit.

Writing this mixed-market source is retired after the 2026-10-01 KRX decision.
Use import_naver_krx_quantity.py for the finalized market/category definition.

Historical quantities come from the preserved HTS workbook. Naver's preserved
trend response supplies ownership ratios for its ten covered dates. The same
Naver parser is used by the live logger and chatbot in tools/investor_flow.py.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from openpyxl import load_workbook

try:
    from tools.investor_flow import parse_trend_rows
    from tools.repair_krx_factors import atomic_write, digest
    from tools.repair_skq_regular_close import patch_rows
except ModuleNotFoundError:
    from investor_flow import parse_trend_rows
    from repair_krx_factors import atomic_write, digest
    from repair_skq_regular_close import patch_rows


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
HTS = BASE / "data/manual_krx/krx_investor_402340.xlsx"
NAVER = BASE / "data/snapshots/20260930/naver_trend_402340.json"
FLOW_FIELDS = ("foreign_net", "institution_net", "individual_net")


def parse_hts(path: Path) -> dict[str, dict[str, str]]:
    sheet = load_workbook(path, read_only=True, data_only=True).active
    rows = sheet.iter_rows(values_only=True)
    heading = next(rows)
    if tuple(heading[:8]) != ("일자", "종가", "전일대비", "등락률", "거래량",
                               "개인", "외국인", "기관계"):
        raise ValueError("Unexpected HTS workbook columns")
    next(rows)  # second header, institution subcategories
    result = {}
    for row in rows:
        raw_date = row[0]
        if raw_date is None:
            continue
        date = str(raw_date).replace("/", "")
        if len(date) != 8 or not date.isdecimal() or date in result:
            raise ValueError(f"Bad or duplicate HTS date: {raw_date!r}")
        values = (row[6], row[7], row[5])  # foreign, institution total, individual
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError(f"Partial HTS flow row: {date}")
        if all(value is None for value in values):
            result[date] = {field: "" for field in FLOW_FIELDS}
            continue
        parsed = {}
        for field, value in zip(FLOW_FIELDS, values):
            if isinstance(value, bool) or int(value) != value:
                raise ValueError(f"Nonintegral HTS quantity: {date} {field}={value!r}")
            parsed[field] = str(int(value))
        result[date] = parsed
    if not result:
        raise ValueError("Empty HTS workbook")
    return result


def plan(factor_path: Path, hts_path: Path, naver_path: Path) -> tuple[bytes, dict]:
    original = factor_path.read_bytes()
    with factor_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != len({row["date"] for row in rows}):
        raise ValueError("Duplicate factor dates")
    hts = parse_hts(hts_path)
    naver = parse_trend_rows(json.loads(naver_path.read_text(encoding="utf-8")), "402340")
    updates = {}
    counts = {"factor_rows": len(rows), "hts_rows": len(hts), "hts_complete": 0,
              "hts_blank": 0, "before_hts": 0, "factor_without_hts": 0,
              "naver_ownership": 0, "naver_flow": 0}
    overlap = {}
    first = min(hts)
    for row in rows:
        date = row["date"]
        if date < first:
            flow = {field: "" for field in FLOW_FIELDS}
            counts["before_hts"] += 1
        elif date in hts:
            flow = hts[date]
            counts["hts_complete" if flow["foreign_net"] != "" else "hts_blank"] += 1
        else:
            flow = {field: "" for field in FLOW_FIELDS}
            counts["factor_without_hts"] += 1
        update = dict(flow)
        if date in naver:
            update["foreign_own_pct"] = str(naver[date]["foreign_own_pct"])
            counts["naver_ownership"] += 1
            if flow["foreign_net"] != "":
                overlap[date] = {
                    field: int(naver[date][field]) - int(flow[field])
                    for field in FLOW_FIELDS
                }
            # The captured Naver volume equals KRX regular-session volume on
            # all ten overlap dates. Use it at the live-source boundary.
            for field in FLOW_FIELDS:
                update[field] = str(naver[date][field])
            counts["naver_flow"] += 1
        updates[date] = update
    patched = patch_rows(original, updates)
    report = {"source": {"hts": str(hts_path.relative_to(BASE)),
                          "naver": str(naver_path.relative_to(BASE))},
              "units": "shares", "counts": counts,
              "first_hts_date": first, "last_hts_date": max(hts),
              "hts_naver_overlap_difference_naver_minus_hts": overlap,
              "factor_before_sha256": digest(original),
              "factor_after_sha256": digest(patched)}
    return patched, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--hts", type=Path, default=HTS)
    parser.add_argument("--naver", type=Path, default=NAVER)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        raise RuntimeError("HTS mixed-market import is retired; use import_naver_krx_quantity.py")
    patched, report = plan(args.factor_log, args.hts, args.naver)
    if args.write:
        if digest(args.factor_log.read_bytes()) != report["factor_before_sha256"]:
            raise RuntimeError("Factor file changed after planning")
        atomic_write(args.factor_log, patched)
        if digest(args.factor_log.read_bytes()) != report["factor_after_sha256"]:
            raise RuntimeError("Written factor hash differs")
    print(json.dumps({"write": args.write, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
