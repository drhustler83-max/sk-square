"""Repair Dreamus split basis and recent KRX closes through the NAV chain.

Offline and dry-run by default. Input KRX rows were captured separately, so
the calculation is reproducible without network access or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from tools.nav_v2 import compute
    from tools.repair_krx_factors import atomic_write, digest
    from tools.repair_skq_regular_close import patch_rows
except ModuleNotFoundError:
    from nav_v2 import compute
    from repair_krx_factors import atomic_write, digest
    from repair_skq_regular_close import patch_rows


BASE = Path(__file__).resolve().parent.parent
LISTED = BASE / "data/listed_holdings_daily.csv"
NAV = BASE / "data/nav_daily.csv"
FACTOR = BASE / "data/factor_log.csv"
SOURCE = BASE / "data/snapshots/20260930/krx_regular_closes_aug03_sep29.json"
OTHER = ("dreamus", "incross", "nanoentek", "krafton", "nexus", "ionq")
ALL = ("skhynix", *OTHER)
TICKER = {"skhynix": "000660", "dreamus": "060570", "nexus": "205500"}
SPLIT = "20260804"
POST_SPLIT_SHARES = 3_286_008


def _number(value: float | int) -> str:
    return str(float(value))


def _updates(old: pd.DataFrame, new: pd.DataFrame, fields: list[str],
             *, atol: float = 0.000001) -> dict[str, dict[str, str]]:
    result = {}
    for i, row in old.iterrows():
        date = row["date"]
        changed = {}
        for field in fields:
            a, b = row[field], new.at[i, field]
            if pd.isna(a) and pd.isna(b):
                continue
            if pd.isna(a) != pd.isna(b) or not np.isclose(a, b, atol=atol, rtol=0):
                changed[field] = "" if pd.isna(b) else _number(b)
        if changed:
            result[date] = changed
    return result


def plan(listed_path: Path, nav_path: Path, factor_path: Path,
         snapshot: dict) -> tuple[dict[str, bytes], dict]:
    if snapshot.get("source") != "KRX Open API regular-session equity daily rows":
        raise ValueError("Unexpected KRX source")
    originals = {"listed": listed_path.read_bytes(), "nav": nav_path.read_bytes(),
                 "factor": factor_path.read_bytes()}
    old_l = pd.read_csv(listed_path, dtype={"date": str})
    old_n = pd.read_csv(nav_path, dtype={"date": str})
    old_f = pd.read_csv(factor_path, dtype={"date": str})
    if (old_l["date"].tolist() != old_n["date"].tolist()
            or len(old_f) != len(set(old_f["date"]))
            or not set(old_l["date"]).issubset(old_f["date"])):
        raise ValueError("Unexpected date coverage or duplicate dates")
    listed = old_l.copy()
    before_split = listed["date"] < SPLIT
    after_split = ~before_split
    counts = set(listed.loc[after_split, "dreamus_shares"].astype(int))
    if counts == {16_430_038}:
        if not (listed.loc[before_split, "dreamus_price"].dropna() % 5 == 0).all():
            raise ValueError("Dreamus historical prices are not 5:1 adjusted")
        listed.loc[before_split, "dreamus_price"] /= 5
        listed.loc[after_split, "dreamus_shares"] = POST_SPLIT_SHARES
    elif counts != {POST_SPLIT_SHARES}:
        raise ValueError("Unexpected pre-repair Dreamus share count")

    source_dates = sorted(snapshot["rows"])
    if not set(source_dates).issubset(listed["date"]):
        raise ValueError("KRX snapshot has date absent from listed holdings")
    source_changes = {key: [] for key in TICKER}
    for date in source_dates:
        i = int(listed.index[listed["date"] == date][0])
        for key, ticker in TICKER.items():
            row = snapshot["rows"][date][ticker]
            if row["BAS_DD"] != date or row["ISU_CD"] != ticker:
                raise ValueError(f"Wrong KRX row: {date} {ticker}")
            volume = int(row["ACC_TRDVOL"].replace(",", ""))
            close = float(row["TDD_CLSPRC"].replace(",", ""))
            if key == "dreamus" and volume == 0:
                # During the halt KRX reports an old reference price, not a
                # fresh close. Keep pre-split raw or post-split adjusted basis.
                continue
            if close <= 0 or volume <= 0:
                raise ValueError(f"No regular-session trade: {date} {ticker}")
            if listed.at[i, key + "_shares"] > 0 and listed.at[i, key + "_price"] != close:
                source_changes[key].append(date)
            listed.at[i, key + "_price"] = close

    for key in TICKER:
        listed[key + "_value"] = listed[key + "_price"] * listed[key + "_shares"]
    # Other unheld names and IONQ are unchanged, but include them in total.
    listed["total_listed_value"] = listed[[key + "_value" for key in ALL]].sum(axis=1)
    listed["total_listed_value_trillion"] = (listed["total_listed_value"] / 1e12).round(4)
    lfields = ["dreamus_shares", "dreamus_price", "dreamus_value",
               "skhynix_price", "skhynix_value", "nexus_price", "nexus_value",
               "total_listed_value", "total_listed_value_trillion"]
    listed_updates = _updates(old_l, listed, lfields, atol=0.5)

    nav = old_n.copy()
    nav["hynix_value"] = listed["skhynix_value"]
    nav["other_listed_value_mtm"] = listed[[key + "_value" for key in OTHER]].sum(axis=1)
    nav["listed_value_mtm"] = nav["hynix_value"] + nav["other_listed_value_mtm"]
    quarters = list(zip(pd.to_datetime(nav["date"]).dt.year,
                        pd.to_datetime(nav["date"]).dt.quarter))
    quarter_end = {}
    for q, value in zip(quarters, nav["other_listed_value_mtm"]):
        quarter_end[q] = value
    frozen = []
    for q, current in zip(quarters, nav["other_listed_value_mtm"]):
        previous = ((q[0] * 4 + q[1] - 2) // 4,
                    (q[0] * 4 + q[1] - 2) % 4 + 1)
        frozen.append(quarter_end.get(previous, current))
    nav["other_listed_value_frozen"] = frozen
    nav["nav_mtm"] = (nav["listed_value_mtm"] + nav["unlisted_value"]
                      + nav["netcash_value"])
    nav["nav_company"] = (nav["hynix_value"] + nav["other_listed_value_frozen"]
                          + nav["unlisted_value"] + nav["netcash_value"])
    nav["nav_per_share_mtm"] = np.round(nav["nav_mtm"] / nav["skq_shares"], 0)
    nav["nav_per_share_company"] = np.round(nav["nav_company"] / nav["skq_shares"], 0)
    nav["nav_discount_pct_mtm"] = np.round(
        (1 - nav["skq_close"] / (nav["nav_mtm"] / nav["skq_shares"])) * 100, 2)
    nav["nav_discount_pct_company"] = np.round(
        (1 - nav["skq_close"] / (nav["nav_company"] / nav["skq_shares"])) * 100, 2)
    for field in ("hynix_value", "other_listed_value_mtm", "listed_value_mtm",
                  "other_listed_value_frozen", "nav_mtm", "nav_company"):
        nav[field] = nav[field].round(0)
    nav["nav_mtm_trillion"] = (nav["nav_mtm"] / 1e12).round(4)
    nav["nav_company_trillion"] = (nav["nav_company"] / 1e12).round(4)
    nfields = ["hynix_value", "other_listed_value_mtm", "listed_value_mtm",
               "other_listed_value_frozen", "nav_mtm", "nav_company",
               "nav_per_share_mtm", "nav_per_share_company",
               "nav_discount_pct_mtm", "nav_discount_pct_company",
               "nav_mtm_trillion", "nav_company_trillion"]
    nav_updates = _updates(old_n, nav, nfields, atol=0.01)

    factor = old_f.copy()
    calculated = compute(nav, nav["date"].map(factor.set_index("date")["skq_ret"]))
    calculated = calculated.set_index("date")
    pairs = {"nav_total_trillion": "nav_total_trillion",
             "nav_implied_ret": "nav_implied_ret",
             "nav_implied_ret_raw": "nav_implied_ret_raw",
             "divergence": "divergence", "nav_discount_pct": "nav_discount_pct",
             "nav_discount_delta": "nav_discount_delta", "nav_per_share": "nav_per_share"}
    for dest, source in pairs.items():
        for i, date in enumerate(factor["date"]):
            if date in calculated.index:
                factor.at[i, dest] = calculated.at[date, source]
    listed_dates = set(listed["date"])
    for i, date in enumerate(factor["date"]):
        if date not in listed_dates:
            continue
        j = int(listed.index[listed["date"] == date][0])
        if j == 0:
            continue
        factor.at[i, "hynix_ret"] = round(
            (listed.at[j, "skhynix_price"] / listed.at[j - 1, "skhynix_price"] - 1) * 100, 2)
    ffields = list(pairs) + ["hynix_ret"]
    factor_updates = _updates(old_f, factor, ffields, atol=0.011)

    patched = {"listed": patch_rows(originals["listed"], listed_updates),
               "nav": patch_rows(originals["nav"], nav_updates),
               "factor": patch_rows(originals["factor"], factor_updates)}
    report = {"source": str(SOURCE.relative_to(BASE)),
              "dreamus_split": {"date": SPLIT, "ratio": "5:1",
                                "post_split_shares": POST_SPLIT_SHARES},
              "krx_close_changed_dates": source_changes,
              "updated_rows": {"listed": len(listed_updates), "nav": len(nav_updates),
                               "factor": len(factor_updates)},
              "updated_columns": {
                  key: dict(Counter(field for row in updates.values() for field in row))
                  for key, updates in (("listed", listed_updates), ("nav", nav_updates),
                                       ("factor", factor_updates))},
              "before_sha256": {key: digest(value) for key, value in originals.items()},
              "after_sha256": {key: digest(value) for key, value in patched.items()}}
    return patched, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listed", type=Path, default=LISTED)
    parser.add_argument("--nav", type=Path, default=NAV)
    parser.add_argument("--factor", type=Path, default=FACTOR)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    snapshot = json.loads(args.source.read_text(encoding="utf-8"))
    files = {"listed": args.listed, "nav": args.nav, "factor": args.factor}
    patched, report = plan(args.listed, args.nav, args.factor, snapshot)
    if args.write:
        for key, path in files.items():
            if digest(path.read_bytes()) != report["before_sha256"][key]:
                raise RuntimeError(f"{key} changed since planning")
        with tempfile.TemporaryDirectory(prefix="nav_rollback_", dir=args.factor.parent) as tmp:
            backup = {}
            for key, path in files.items():
                backup[key] = Path(tmp) / path.name
                shutil.copy2(path, backup[key])
            written = []
            try:
                for key, path in files.items():
                    atomic_write(path, patched[key])
                    written.append(key)
            except BaseException:
                for key in reversed(written):
                    os.replace(backup[key], files[key])
                raise
        for key, path in files.items():
            if digest(path.read_bytes()) != report["after_sha256"][key]:
                raise RuntimeError(f"{key} hash differs after write")
    print(json.dumps({"write": args.write, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
