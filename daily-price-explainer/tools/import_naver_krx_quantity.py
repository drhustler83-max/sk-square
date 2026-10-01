"""Plan an exact KRX-share F3 migration from a hash-verified Naver PC capture.

foreign_net includes foreign + other-foreign; institution/individual are KRX
net shares. Ownership ratios fill blanks only. No price fields are imported.
The 2022-11-09~12-29 flow gap is preserved by explicit user decision.
Default is dry-run. --write requires the planned factor SHA-256.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.investor_flow import parse_pc_trend_rows
from tools.repair_krx_factors import atomic_write, digest
from tools.repair_skq_regular_close import patch_rows

FLOW_FIELDS = ("foreign_net", "institution_net", "individual_net")


def plan(factor_path: Path, snapshot_path: Path) -> tuple[bytes, dict]:
    original = factor_path.read_bytes()
    raw = snapshot_path.read_bytes()
    manifest = json.loads((snapshot_path.parent / "capture_manifest.json").read_text(encoding="utf-8"))
    captures = [r for r in manifest if r["file"] == snapshot_path.name]
    if len(captures) != 1 or captures[0]["sha256"] != digest(raw):
        raise ValueError("Snapshot missing from manifest or hash differs")
    source_url = urlparse(captures[0]["url"])
    if (source_url.scheme != "https" or source_url.netloc != "stock.naver.com"
            or source_url.path != "/api/domestic/detail/402340/trend"
            or parse_qs(source_url.query).get("tradeType") != ["KRX"]):
        raise ValueError("Snapshot is not an explicit Naver KRX capture")
    source = parse_pc_trend_rows(json.loads(raw), "402340")
    with factor_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    dates = [row["date"] for row in rows]
    if len(dates) != len(set(dates)) or set(dates) != set(source):
        raise ValueError("Full migration requires identical unique factor/source dates")
    updates, filled, changed, changes, skipped = {}, Counter(), Counter(), [], []
    for row in rows:
        date = row["date"]
        if "20221109" <= date <= "20221229":
            if any(row[f].strip() for f in FLOW_FIELDS):
                raise ValueError("Preserved 2022 gap unexpectedly contains flow values")
            update = {}
            skipped.append(date)
        else:
            update = {f: str(source[date][f]) for f in FLOW_FIELDS}
        if not row["foreign_own_pct"].strip():
            update["foreign_own_pct"] = str(source[date]["foreign_own_pct"])
        for field, value in update.items():
            old = row[field]
            if not old.strip():
                filled[field] += 1
            # Count numerical changes, not harmless formatting differences.
            if not old.strip() or float(old) != float(value):
                changed[field] += 1
                changes.append({"date": date, "field": field, "before": old, "after": value})
        updates[date] = update
    patched = patch_rows(original, updates)
    return patched, {
        "source": str(snapshot_path.relative_to(BASE)) if snapshot_path.is_relative_to(BASE) else str(snapshot_path),
        "source_url": captures[0]["url"], "source_sha256": digest(raw),
        "definition": {"market": "KRX", "unit": "shares",
                       "foreign_net": "foreign + other-foreign net shares",
                       "institution_net": "institution-total net shares",
                       "individual_net": "individual net shares"},
        "rows": len(rows), "range": [min(dates), max(dates)],
        "flow_gap_preserved_by_user": skipped,
        "filled_cells": dict(filled), "numerically_changed_cells": dict(changed),
        "factor_before_sha256": digest(original), "factor_after_sha256": digest(patched),
        "changes": changes,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--factor-log", type=Path, default=BASE / "data/factor_log.csv")
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--json", type=Path)
    p.add_argument("--write", action="store_true")
    p.add_argument("--expected-sha256")
    args = p.parse_args()
    if args.json and (args.json.suffix.lower() != ".json"
                      or args.json.resolve() in (args.factor_log.resolve(), args.snapshot.resolve())):
        raise ValueError("Report must be a separate JSON file")
    patched, report = plan(args.factor_log, args.snapshot)
    if args.write:
        if args.expected_sha256 != report["factor_before_sha256"]:
            raise ValueError("--write requires the exact planned --expected-sha256")
        if digest(args.factor_log.read_bytes()) != report["factor_before_sha256"]:
            raise RuntimeError("Factor changed after planning")
        atomic_write(args.factor_log, patched)
        if digest(args.factor_log.read_bytes()) != report["factor_after_sha256"]:
            raise RuntimeError("Written factor hash differs")
    if args.json:
        atomic_write(args.json, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps({k: v for k, v in report.items() if k != "changes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
