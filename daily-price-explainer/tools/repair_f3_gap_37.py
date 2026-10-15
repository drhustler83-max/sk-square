"""Fill the 2022-11-09~12-29 F3 gap from the hash-verified Naver PC KRX capture.

These 37 days were left blank on 2026-10-01 because the HTS export's quantity
column was empty for them, and at that point HTS looked like the only source.
The rest of the series was later unified onto the Naver PC KRX capture, which
does carry these dates, so the gap is a stale decision rather than missing data
(Sean, 2026-10-15). Same source, same definition as the other 1,146 days.

Only the three flow columns on those 37 dates are touched, and only if blank.

Usage:
  python tools/repair_f3_gap_37.py                  # dry run
  python tools/repair_f3_gap_37.py --apply --expected-sha256 HASH
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.investor_flow import parse_pc_trend_rows
from tools.repair_krx_factors import atomic_write, digest
from tools.repair_skq_regular_close import patch_rows

FACTOR = BASE / "data/factor_log.csv"
SNAPSHOT = BASE / "data/snapshots/20261001/f3_source_scope/naver_pc_krx_0_1200.json"
FLOW_FIELDS = ("foreign_net", "institution_net", "individual_net")
GAP_START, GAP_END = "20221109", "20221229"


def load_source(snapshot_path: Path) -> dict[str, dict]:
    raw = snapshot_path.read_bytes()
    manifest = json.loads(
        (snapshot_path.parent / "capture_manifest.json").read_text(encoding="utf-8"))
    captures = [r for r in manifest if r["file"] == snapshot_path.name]
    if len(captures) != 1 or captures[0]["sha256"] != digest(raw):
        raise ValueError("Snapshot missing from manifest or hash differs")
    url = urlparse(captures[0]["url"])
    if (url.scheme != "https" or url.netloc != "stock.naver.com"
            or url.path != "/api/domestic/detail/402340/trend"
            or parse_qs(url.query).get("tradeType") != ["KRX"]):
        raise ValueError("Snapshot is not an explicit Naver KRX capture")
    return parse_pc_trend_rows(json.loads(raw), "402340")


def plan(factor_path: Path, snapshot_path: Path) -> tuple[bytes, bytes, dict]:
    original = factor_path.read_bytes()
    source = load_source(snapshot_path)
    with factor_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    updates, changes = {}, []
    for row in rows:
        date = row["date"]
        if not (GAP_START <= date <= GAP_END):
            continue
        if any(row[f].strip() for f in FLOW_FIELDS):
            raise ValueError(f"{date} already has flow values — refusing to overwrite")
        if date not in source:
            raise ValueError(f"{date} not present in the capture")
        updates[date] = {f: str(source[date][f]) for f in FLOW_FIELDS}
        changes.append({"date": date, **updates[date]})

    if not updates:
        raise ValueError("No blank gap rows found")
    patched = patch_rows(original, updates)
    return original, patched, {
        "source": str(snapshot_path.relative_to(BASE)),
        "source_sha256": digest(snapshot_path.read_bytes()),
        "definition": {"market": "KRX", "unit": "shares",
                       "foreign_net": "foreign + other-foreign net shares"},
        "filled_dates": len(updates), "values": changes,
        "before_sha256": digest(original), "after_sha256": digest(patched),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    original, patched, report = plan(args.factor_log, args.snapshot)
    if args.expected_sha256 and report["before_sha256"] != args.expected_sha256:
        raise ValueError("Factor log changed since expected SHA-256")
    if args.apply:
        if digest(args.factor_log.read_bytes()) != report["before_sha256"]:
            raise ValueError("Factor log changed during planning")
        atomic_write(args.factor_log, patched)
        if digest(args.factor_log.read_bytes()) != report["after_sha256"]:
            raise ValueError("Write verification failed")
    print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
