"""Safely repair only regime/divergence gaps in data/event_residual.csv.

The master CSV contains manual curation in columns G:K, so this utility:

* never invokes the event-residual rebuild pipeline;
* changes only blank ``regime`` and ``divergence`` cells;
* validates the reconstructed divergence formula against populated rows first;
* requires ``--apply`` before writing.

The historical NAV formula mirrors tools.nav using the current COMPANY_CONTEXT:
listed holdings at their daily close plus fixed unlisted/net-cash book values.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import mean

from pykrx import stock

from memory.company_context import COMPANY_CONTEXT


BASE = Path(__file__).resolve().parent.parent
MASTER_PATH = BASE / "data" / "event_residual.csv"


def _read_master() -> tuple[list[str], list[dict[str, str]]]:
    with MASTER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _listed_price_maps(start: str, end: str) -> dict[str, dict[str, int]]:
    maps: dict[str, dict[str, int]] = {}
    for holding in COMPANY_CONTEXT["nav_holdings"]:
        if not holding.get("listed") or not holding.get("ticker"):
            continue
        ticker = holding["ticker"]
        if ticker == "000660":
            # SK hynix closes already live in the human-maintained master.
            continue
        frame = stock.get_market_ohlcv(start, end, ticker)
        maps[ticker] = {
            index.strftime("%Y%m%d"): int(row["종가"])
            for index, row in frame.iterrows()
        }
    return maps


def _nav(row: dict[str, str], price_maps: dict[str, dict[str, int]]) -> int:
    total = 0
    date = row["date"]
    for holding in COMPANY_CONTEXT["nav_holdings"]:
        if holding.get("listed") and holding.get("ticker"):
            ticker = holding["ticker"]
            if ticker == "000660":
                price = int(row["hynix_price"])
            else:
                price = price_maps[ticker][date]
            total += price * int(holding["shares_held"])
        else:
            total += int(holding.get("book_value", 0))
    return total


def _metrics(
    previous: dict[str, str],
    current: dict[str, str],
    price_maps: dict[str, dict[str, int]],
) -> tuple[str, float]:
    skq_return = round(
        (int(current["skq_price"]) / int(previous["skq_price"]) - 1) * 100, 2
    )
    hynix_return = round(
        (int(current["hynix_price"]) / int(previous["hynix_price"]) - 1) * 100, 2
    )
    nav_return = round((_nav(current, price_maps) / _nav(previous, price_maps) - 1) * 100, 2)
    divergence = round(skq_return - nav_return, 2)

    if skq_return == 0 or hynix_return == 0:
        regime = "Zero"
    elif (skq_return > 0) != (hynix_return > 0):
        regime = "교차"
    else:
        regime = "동일"
    return regime, divergence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write validated repairs")
    args = parser.parse_args()

    fieldnames, rows = _read_master()
    expected = [
        "date",
        "skq_price",
        "hynix_price",
        "regime",
        "divergence",
        "high_residual",
        "event_name",
        "direction",
        "importance",
        "note",
        "source",
    ]
    if fieldnames != expected:
        raise RuntimeError(f"Unexpected master schema: {fieldnames}")

    price_maps = _listed_price_maps(rows[0]["date"], rows[-1]["date"])
    calculated: dict[str, tuple[str, float]] = {}
    for index in range(1, len(rows)):
        calculated[rows[index]["date"]] = _metrics(rows[index - 1], rows[index], price_maps)

    errors = [
        abs(float(row["divergence"]) - calculated[row["date"]][1])
        for row in rows[1:]
        if row["divergence"].strip()
    ]
    exact = sum(error < 0.005 for error in errors)
    within_one_basis_point = sum(error <= 0.011 for error in errors)
    print(
        "validation:",
        f"n={len(errors)}",
        f"exact={exact / len(errors):.1%}",
        f"within_0.01pp={within_one_basis_point / len(errors):.1%}",
        f"mae={mean(errors):.4f}pp",
        f"max={max(errors):.2f}pp",
    )
    if exact / len(errors) < 0.95 or max(errors) > 0.02:
        raise RuntimeError("Reconstructed formula does not match populated master rows")

    repairs: list[tuple[str, str, float]] = []
    for row in rows[1:]:
        if not row["regime"].strip() and not row["divergence"].strip():
            regime, divergence = calculated[row["date"]]
            repairs.append((row["date"], regime, divergence))

    print(f"repairable={len(repairs)}; unrepairable_first_listing_day=1")
    for date, regime, divergence in repairs:
        print(f"{date},{regime},{divergence:.2f}")

    if not args.apply:
        print("dry-run only; pass --apply to write")
        return

    before_protected = [
        tuple(row[column] for column in expected if column not in ("regime", "divergence"))
        for row in rows
    ]
    repair_map = {date: (regime, divergence) for date, regime, divergence in repairs}
    for row in rows:
        if row["date"] in repair_map:
            regime, divergence = repair_map[row["date"]]
            row["regime"] = regime
            row["divergence"] = f"{divergence:.2f}".rstrip("0").rstrip(".")

    after_protected = [
        tuple(row[column] for column in expected if column not in ("regime", "divergence"))
        for row in rows
    ]
    if before_protected != after_protected:
        raise RuntimeError("Protected columns changed in memory; refusing to write")

    with MASTER_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(repairs)} D/E repairs to {MASTER_PATH}")


if __name__ == "__main__":
    main()
