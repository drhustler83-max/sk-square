"""Read-only F3 source audit using preserved HTS, Naver and KRX data.

This reports market and investor-category differences without patching factors
or assuming that matching total volume proves matching investor definitions.
Run: .venv/Scripts/python tools/audit_investor_source_scope.py --json REPORT.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.import_investor_quantity import FLOW_FIELDS, parse_hts
from tools.investor_flow import parse_pc_trend_rows
from tools.repair_krx_factors import atomic_write, digest

CAPTURE = BASE / "data/snapshots/20261001/f3_source_scope"
HTS = BASE / "data/manual_krx/krx_investor_402340.xlsx"
FACTORS = BASE / "data/factor_log.csv"


def integer(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Missing/nonintegral quantity: {value!r}")
    text = str(value).replace(",", "").replace("+", "")
    parsed = int(text)
    return parsed


def naver_rows(path: Path) -> dict[str, dict]:
    result = {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"No captured Naver rows: {path}")
    for row in rows:
        date = row["bizdate"]
        if row["itemCode"] != "402340" or date in result:
            raise ValueError(f"Unexpected/duplicate Naver row: {date}")
        result[date] = row
    return result


def audit() -> dict:
    protected = [FACTORS, BASE / "data/nav_daily.csv",
                 BASE / "data/listed_holdings_daily.csv", HTS]
    before = {str(p.relative_to(BASE)): digest(p.read_bytes()) for p in protected}
    manifest = json.loads((CAPTURE / "capture_manifest.json").read_text(encoding="utf-8"))
    for source in manifest:
        if digest((CAPTURE / source["file"]).read_bytes()) != source["sha256"]:
            raise ValueError(f"Capture hash mismatch: {source['file']}")
    flows = parse_hts(HTS)
    workbook = load_workbook(HTS, read_only=True, data_only=True)
    try:
        sheet_rows = list(workbook.active.values)
        hts = {str(row[0]).replace("/", ""): row for row in sheet_rows[2:] if row[0]}
        created = workbook.properties.created
        modified = workbook.properties.modified
    finally:
        workbook.close()
    with FACTORS.open(encoding="utf-8-sig", newline="") as file:
        factors = {row["date"]: row for row in csv.DictReader(file)}
    blanks = sorted(d for d, row in flows.items() if all(row[f] == "" for f in FLOW_FIELDS))
    if len(hts) != len(flows):
        raise ValueError("Unexpected workbook row count")

    krx = {}
    sources = sorted((BASE / "data/snapshots/krx_regular_history").glob("krx_regular_*.json"))
    sources.append(BASE / "data/snapshots/20260930/krx_regular_closes_aug03_sep29.json")
    for source in sources:
        capture = json.loads(source.read_text(encoding="utf-8"))
        if capture["source"] != "KRX Open API regular-session equity daily rows":
            raise ValueError(f"Unexpected KRX source: {source}")
        for date, tickers in capture["rows"].items():
            if "402340" not in tickers:
                continue
            row = tickers["402340"]
            if row["ISU_CD"] != "402340" or row["BAS_DD"] != date:
                raise ValueError(f"KRX date/ticker mismatch: {date}")
            parsed = {"close": integer(row["TDD_CLSPRC"]), "volume": integer(row["ACC_TRDVOL"])}
            if date in krx and krx[date] != parsed:
                raise ValueError(f"Conflicting KRX captures: {date}")
            krx[date] = parsed
    history = []
    for date in sorted(set(hts) & set(krx)):
        row = hts[date]
        history.append({"date": date, "hts_volume": integer(row[4]),
                        "krx_volume": krx[date]["volume"],
                        "volume_difference_hts_minus_krx": integer(row[4]) - krx[date]["volume"],
                        "hts_close": integer(row[1]), "krx_close": krx[date]["close"]})

    k = naver_rows(CAPTURE / "naver_pc_krx_0_60.json")
    n = naver_rows(CAPTURE / "naver_pc_nxt_0_60.json")
    if set(k) != set(n) or not set(k) <= set(hts):
        raise ValueError("Naver market/workbook dates differ")
    comparisons = []
    for date in sorted(k):
        row = hts[date]
        values = {
            "volume": (integer(row[4]), "tradeVolume"),
            "institution": (integer(row[7]), "organPureBuyQuant"),
            "individual": (integer(row[5]), "individualPureBuyQuant"),
            "foreign_registered": (integer(row[6]), "foreignerPureBuyQuant"),
            "foreign_including_other": (integer(row[6]) + (integer(row[16]) if row[16] is not None else 0),
                                        "foreignerPureBuyQuant"),
        }
        compared = {"date": date, "metrics": {}}
        for name, (actual, field) in values.items():
            krx_value, nxt_value = integer(k[date][field]), integer(n[date][field])
            compared["metrics"][name] = {
                "hts": actual, "naver_krx": krx_value, "naver_nxt": nxt_value,
                "hts_minus_krx_plus_nxt": actual - krx_value - nxt_value,
            }
        if date in krx:
            compared["naver_krx_volume_matches_official_krx"] = integer(k[date]["tradeVolume"]) == krx[date]["volume"]
        comparisons.append(compared)
    # The last workbook date differs from the final combined-market totals.
    # Report it separately; the timestamp alone does not establish its cause.
    settled = [r for r in comparisons if r["date"] < max(hts)]
    match_counts = {name: sum(row["metrics"][name]["hts_minus_krx_plus_nxt"] == 0 for row in settled)
                    for name in comparisons[0]["metrics"]}
    full_path = CAPTURE / "naver_pc_krx_0_1200.json"
    full_raw = naver_rows(full_path)
    full = parse_pc_trend_rows(list(full_raw.values()), "402340")
    if set(full) != set(factors):
        raise ValueError("Full Naver capture does not exactly cover factor dates")
    volume_mismatches = [r["date"] for r in history
                         if integer(full_raw[r["date"]]["tradeVolume"]) != r["krx_volume"]]
    pre_ats = []
    for date in sorted(hts):
        row = hts[date]
        if date >= "20250304" or row[6] is None:
            continue
        pre_ats.append({"date": date,
                        "foreign_difference_naver_minus_hts_including_other": full[date]["foreign_net"] - integer(row[6]) - (integer(row[16]) if row[16] is not None else 0),
                        "institution_difference_naver_minus_hts": full[date]["institution_net"] - integer(row[7]),
                        "individual_difference_naver_minus_hts": full[date]["individual_net"] - integer(row[5])})
    after = {str(p.relative_to(BASE)): digest(p.read_bytes()) for p in protected}
    if before != after:
        raise RuntimeError("Protected source file changed during read-only audit")
    return {
        "units": "shares", "protected_before_sha256": before,
        "protected_after_sha256": after, "protected_files_unchanged": True,
        "source_sha256": {str(p.relative_to(BASE)): digest(p.read_bytes()) for p in sources},
        "workbook": {"rows": len(hts), "range": [min(hts), max(hts)],
                     "created_naive": created.isoformat() if created else None,
                     "modified_naive": modified.isoformat() if modified else None,
                     "timestamp_timezone": "not recorded in workbook; do not infer timezone",
                     "market_setting_recorded": False, "broker_screen_recorded": False,
                     "first_complete_flow_date": min(d for d, r in flows.items() if r["foreign_net"] != ""),
                     "blank_flow_dates": blanks,
                     "blank_dates_still_missing_in_factor": {f: [d for d in blanks if not factors[d][f].strip()] for f in FLOW_FIELDS}},
        "full_naver_krx_source": {
            "file": str(full_path.relative_to(BASE)), "sha256": digest(full_path.read_bytes()),
            "rows": len(full), "range": [min(full), max(full)],
            "covers_all_factor_dates": True, "covers_all_37_workbook_blank_dates": set(blanks) <= set(full),
            "official_krx_volume_comparisons": len(history), "official_krx_volume_mismatch_dates": volume_mismatches,
            "pre_ats_hts_comparison_rows": len(pre_ats),
            "pre_ats_difference_counts": {name: sum(r[name] != 0 for r in pre_ats)
                                           for name in pre_ats[0] if name != "date"},
            "pre_ats_comparisons": pre_ats,
            "pre_ats_difference_note": "Residual differences exist even after adding other-foreign before ATS launch; exact session/revision cause is unverified. Use the chosen native Naver definition consistently, never derive missing quantities from prices."},
        "hts_official_krx_history": {
            "rows": len(history), "range": [history[0]["date"], history[-1]["date"]],
            "volume_relation_counts": dict(Counter("equal" if r["volume_difference_hts_minus_krx"] == 0
                                                   else "hts_greater" if r["volume_difference_hts_minus_krx"] > 0
                                                   else "hts_smaller" for r in history)),
            "first_volume_difference_date": next((r["date"] for r in history if r["volume_difference_hts_minus_krx"] != 0), None),
            "comparisons": history},
        "naver_krx_nxt_accounting": {
            "all_rows": len(comparisons), "settled_rows_before_workbook_last_date": len(settled),
            "settled_range": [settled[0]["date"], settled[-1]["date"]],
            "settled_exact_match_counts": match_counts, "comparisons": comparisons,
            "limits": ["Exact accounting establishes the observed dates, not all historical investor rows.",
                       "HTS broker/screen and original market selector still require user provenance.",
                       "The final workbook date is excluded from settled counts and remains visible above.",
                       "Naver trend price fields are not a KRX regular-close source.",
                       "The full KRX capture covers 2022 missing quantities; this audit never changes factor values."]},
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = audit()
    if args.json:
        resolved = args.json.resolve()
        if resolved.suffix.lower() != ".json" or resolved in (p.resolve() for p in BASE.glob("data/*.csv")):
            raise ValueError("Audit output must be a separate JSON report")
        atomic_write(resolved, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    summary = {"hts_blank_flow_days": len(report["workbook"]["blank_flow_dates"]),
               "first_complete_flow_date": report["workbook"]["first_complete_flow_date"],
               "hts_krx_volume_relation_counts": report["hts_official_krx_history"]["volume_relation_counts"],
               "settled_accounting_matches": report["naver_krx_nxt_accounting"]["settled_exact_match_counts"],
               "protected_files_unchanged": report["protected_files_unchanged"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
