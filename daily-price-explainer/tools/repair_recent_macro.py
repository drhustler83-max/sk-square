"""Restore recent KOSPI and USD/KRW gaps from saved exact-date responses."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.regular_macro import _rate, bok_usd_krw, krx_market_factors
    from tools.repair_krx_factors import atomic_write
except ModuleNotFoundError:
    from regular_macro import _rate, bok_usd_krw, krx_market_factors
    from repair_krx_factors import atomic_write


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
COLUMNS = ("kospi_ret", "usd_krw")


def factor_dates(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return [row["date"] for row in rows if row["date"] >= "20260917"
            and any(not row[column] for column in COLUMNS)]


def capture(dates: list[str]) -> dict:
    rows = {}
    for date in dates:
        krx_values, krx_rows = krx_market_factors(date)
        usd, bok_row = bok_usd_krw(date)
        rows[date] = {"values": {"kospi_ret": krx_values["kospi_ret"],
                                 "usd_krw": usd},
                      "krx": krx_rows, "bok": bok_row}
    return {"source": {"kospi": "KRX Open API idx/kospi_dd_trd",
                       "usd_krw": "BOK ECOS 731Y001/0000001"},
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "rows": rows}


def plan(path: Path, snapshot: dict) -> tuple[bytes, bytes, dict]:
    if snapshot.get("source") != {"kospi": "KRX Open API idx/kospi_dd_trd",
                                  "usd_krw": "BOK ECOS 731Y001/0000001"}:
        raise ValueError("Unexpected macro source")
    rows = snapshot["rows"]
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    header = lines[0].decode("utf-8-sig").strip().split(",")
    indexes = {column: header.index(column) for column in COLUMNS}
    seen, patched, filled = set(), [], {column: 0 for column in COLUMNS}
    for line in lines:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        cells = line[:-len(ending)].split(b",") if ending else line.split(b",")
        date = cells[0].decode("ascii")
        if date in rows:
            if len(cells) != len(header) or date < "20260917":
                raise ValueError(f"Unexpected factor row on {date}")
            source = rows[date]
            if source["krx"]["kospi"]["BAS_DD"] != date or source["bok"]["TIME"] != date:
                raise ValueError(f"Wrong source date on {date}")
            if (source["krx"]["kospi"]["IDX_NM"] != "코스피"
                    or source["bok"]["STAT_CODE"] != "731Y001"
                    or source["bok"]["ITEM_CODE1"] != "0000001"
                    or source["values"]["kospi_ret"] != _rate(
                        source["krx"]["kospi"], "CLSPRC_IDX", "CMPPREVDD_IDX")
                    or source["values"]["usd_krw"] != float(
                        str(source["bok"]["DATA_VALUE"]).replace(",", ""))):
                raise ValueError(f"Macro snapshot values disagree with source on {date}")
            for column in COLUMNS:
                value = source["values"][column]
                encoded = (f"{value:.2f}" if column == "kospi_ret" else f"{value:g}").encode("ascii")
                position = indexes[column]
                if cells[position] == b"":
                    cells[position] = encoded
                    filled[column] += 1
                elif cells[position] != encoded:
                    raise ValueError(f"Conflicting {column} value on {date}")
            line = b",".join(cells) + ending
            seen.add(date)
        patched.append(line)
    if seen != set(rows):
        raise ValueError("Snapshot dates do not match factor log")
    result = b"".join(patched)
    digest = lambda data: hashlib.sha256(data).hexdigest()
    return original, result, {"dates": sorted(rows), "filled": filled,
                              "before_sha256": digest(original),
                              "after_sha256": digest(result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--snapshot", type=Path)
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.capture:
        if args.apply:
            parser.error("--capture cannot be combined with --apply")
        report = capture(factor_dates(args.factor_log))
        atomic_write(args.capture, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
        print(json.dumps({"captured": len(report["rows"]), "path": str(args.capture)}))
        return
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    original, result, report = plan(args.factor_log, snapshot)
    if args.apply:
        if args.factor_log.read_bytes() != original:
            raise ValueError("Factor log changed since planning")
        if original != result:
            atomic_write(args.factor_log, result)
        if args.factor_log.read_bytes() != result:
            raise ValueError("Factor write verification failed")
    if args.report:
        atomic_write(args.report, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
