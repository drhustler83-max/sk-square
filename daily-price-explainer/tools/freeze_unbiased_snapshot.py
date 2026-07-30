"""
Freeze and audit event_residual_unbiased.csv without modifying the source.

Usage:
  python tools/freeze_unbiased_snapshot.py --snapshot-date 20260729
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "data" / "event_residual_unbiased.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit(path: Path) -> dict:
    df = pd.read_csv(path, dtype={"date": str})
    high = df["high_residual"].fillna("").eq("Y")
    has_event = df["event_name"].notna() & df["event_name"].astype(str).str.strip().ne("")
    expected_high = df["divergence"].abs().ge(3)

    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "date_min": str(df["date"].min()),
        "date_max": str(df["date"].max()),
        "duplicate_dates": int(df["date"].duplicated().sum()),
        "dates_sorted": bool(df["date"].is_monotonic_increasing),
        "missing": {name: int(df[name].isna().sum()) for name in df.columns},
        "high_residual_rows": int(high.sum()),
        "event_name_rows": int(has_event.sum()),
        "high_flag_mismatches": int((high != expected_high).sum()),
        "selection_cross_tab": {
            "event_false_high_false": int((~has_event & ~high).sum()),
            "event_false_high_true": int((~has_event & high).sum()),
            "event_true_high_false": int((has_event & ~high).sum()),
            "event_true_high_true": int((has_event & high).sum()),
        },
        "event_rate_on_high_residual": round(float(has_event[high].mean()), 6),
        "event_rate_on_non_high_residual": round(float(has_event[~high].mean()), 6),
    }


def freeze(snapshot_date: str) -> dict:
    snapshot_dir = BASE / "data" / "snapshots" / snapshot_date
    snapshot = snapshot_dir / SOURCE.name
    manifest_path = snapshot_dir / "manifest.json"

    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)

    source_hash = _sha256(SOURCE)
    if snapshot_dir.exists():
        if not snapshot.exists() or _sha256(snapshot) != source_hash:
            raise FileExistsError(
                f"Snapshot directory already exists with different contents: {snapshot_dir}"
            )
    else:
        snapshot_dir.mkdir(parents=True)
        shutil.copy2(SOURCE, snapshot)

    snapshot_hash = _sha256(snapshot)
    if snapshot_hash != source_hash:
        raise RuntimeError("Snapshot hash mismatch")

    manifest = {
        "snapshot_version": "event_residual_unbiased_snapshot.v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(SOURCE.relative_to(BASE)).replace("\\", "/"),
        "snapshot": str(snapshot.relative_to(BASE)).replace("\\", "/"),
        "sha256": source_hash,
        "size_bytes": SOURCE.stat().st_size,
        "source_mtime": datetime.fromtimestamp(
            SOURCE.stat().st_mtime
        ).astimezone().isoformat(timespec="seconds"),
        "immutability_policy": (
            "Do not overwrite event_residual_unbiased.csv. Create derivative files only."
        ),
        "audit": _audit(SOURCE),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot-date",
        default=datetime.now().strftime("%Y%m%d"),
        help="Snapshot directory name in YYYYMMDD format.",
    )
    args = parser.parse_args()
    print(json.dumps(freeze(args.snapshot_date), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
