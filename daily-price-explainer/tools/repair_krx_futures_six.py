"""Repair six spread-contract selection errors in historical SK Square futures.

The KRX source is captured separately. Applying never calls the network and only
patches the six specified dates after verifying the NAV and KRX spot closes agree.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

try:
    from tools.repair_krx_factors import atomic_write, digest
except ModuleNotFoundError:  # direct `python tools/repair_krx_futures_six.py`
    from repair_krx_factors import atomic_write, digest


BASE = Path(__file__).resolve().parent.parent
FACTOR = BASE / "data/factor_log.csv"
NAV = BASE / "data/nav_daily.csv"
DATES = ("20230410", "20230509", "20230810", "20240409", "20240502", "20240509")
FIELDS = ("fut_basis", "fut_basis_pct", "fut_volume")


def capture(dates: tuple[str, ...] = DATES) -> dict:
    import requests
    import truststore
    from dotenv import dotenv_values

    truststore.inject_into_ssl()
    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    session = requests.Session()
    session.headers["AUTH_KEY"] = key
    source = {}
    for date in dates:
        def rows(endpoint: str) -> list[dict]:
            response = session.get("https://data-dbg.krx.co.kr/svc/apis/" + endpoint,
                                   params={"basDd": date}, timeout=20)
            response.raise_for_status()
            result = response.json().get("OutBlock_1")
            if not isinstance(result, list):
                raise ValueError(f"Missing KRX response on {date}")
            return result

        spot = [r for r in rows("sto/stk_bydd_trd") if r.get("ISU_CD") == "402340"]
        contracts = [r for r in rows("drv/eqsfu_stk_bydd_trd")
                     if r.get("PROD_NM") == "SK스퀘어 선물"]
        if len(spot) != 1 or not contracts:
            raise ValueError(f"Missing SK Square stock or contracts on {date}")
        source[date] = {
            "spot": {k: spot[0][k] for k in ("BAS_DD", "ISU_CD", "ISU_NM", "TDD_CLSPRC")},
            "contracts": [{k: r[k] for k in
                           ("BAS_DD", "PROD_NM", "ISU_CD", "ISU_NM", "TDD_CLSPRC", "ACC_TRDVOL")}
                          for r in contracts],
        }
    return {"source": "KRX Open API", "dates": source}


def plan(factor_path: Path, nav_path: Path, snapshot: dict,
         dates: tuple[str, ...] = DATES, mode: str = "spread",
         spot_policy: str = "match_nav") -> tuple[bytes, bytes, dict]:
    if snapshot.get("source") != "KRX Open API" or set(snapshot.get("dates", {})) != set(dates):
        raise ValueError("Snapshot date/source mismatch")
    with nav_path.open(encoding="utf-8-sig", newline="") as file:
        nav = {row["date"]: row for row in csv.DictReader(file)}
    with factor_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        columns, rows = reader.fieldnames, {row["date"]: row for row in reader}
    if not columns or not set(FIELDS).issubset(columns):
        raise ValueError("Unexpected factor columns")
    values = {}
    chosen = {}
    for date in dates:
        entry = snapshot["dates"][date]
        spot_row = entry["spot"]
        spot = float(spot_row["TDD_CLSPRC"].replace(",", ""))
        nav_close = float(nav[date]["skq_close"])
        if spot_row["BAS_DD"] != date or spot <= 0:
            raise ValueError(f"Bad KRX spot on {date}")
        if spot_policy == "match_nav" and spot != nav_close:
            raise ValueError(f"KRX and NAV spot disagree on {date}")
        if spot_policy not in ("match_nav", "krx"):
            raise ValueError("Unknown spot policy")
        listed = rows[date]["fut_listed"]
        if mode == "spread" and listed not in ("1", "1.0"):
            raise ValueError(f"Expected listed futures on {date}")
        if mode == "gap" and listed not in ("", "0", "0.0"):
            raise ValueError(f"Expected empty/incorrect listed flag on {date}")
        if rows[date]["fut_basis"] or rows[date]["fut_basis_pct"]:
            raise ValueError(f"Existing basis is not empty on {date}")
        if mode == "gap" and rows[date]["fut_volume"]:
            raise ValueError(f"Existing volume is not empty on {date}")
        outright = [r for r in entry["contracts"]
                    if r["BAS_DD"] == date and r["PROD_NM"] == "SK스퀘어 선물"
                    and re.search(r"\bF\s+\d{6}\b", r["ISU_NM"])
                    and r["TDD_CLSPRC"].strip()
                    and float(r["TDD_CLSPRC"].replace(",", "")) > 0]
        if not outright:
            raise ValueError(f"No valid outright contract on {date}")
        near = max(outright, key=lambda r: int(r["ACC_TRDVOL"].replace(",", "")))
        close = float(near["TDD_CLSPRC"].replace(",", ""))
        volume = int(near["ACC_TRDVOL"].replace(",", ""))
        basis = close - spot
        values[date] = {"fut_basis": str(basis),
                        "fut_basis_pct": str(round(basis / spot * 100, 3)),
                        "fut_volume": str(float(volume))}
        if mode == "gap":
            values[date]["fut_listed"] = "1.0"
        chosen[date] = {"contract": near["ISU_NM"].strip(), "spot": spot,
                        "saved_nav_close": nav_close,
                        "future_close": close, "future_volume": volume,
                        "previous_listed": listed,
                        "previous_volume": rows[date]["fut_volume"]}
    original = factor_path.read_bytes()
    lines = original.splitlines(keepends=True)
    if len(lines) != len(rows) + 1:
        raise ValueError("Unexpected physical CSV line count")
    patched, changed = [], set()
    for line in lines:
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        cells = line[:-len(ending)].split(b",") if ending else line.split(b",")
        date = cells[0].decode("ascii")
        if date in values:
            if len(cells) != len(columns):
                raise ValueError(f"Unexpected CSV row on {date}")
            for name, value in values[date].items():
                cells[columns.index(name)] = value.encode("ascii")
            line = b",".join(cells) + ending
            changed.add(date)
        patched.append(line)
    if changed != set(dates):
        raise ValueError("Not every date was patched")
    result = b"".join(patched)
    return original, result, {"dates": chosen, "before_sha256": digest(original),
                             "after_sha256": digest(result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-log", type=Path, default=FACTOR)
    parser.add_argument("--nav", type=Path, default=NAV)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--snapshot", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--mode", choices=("spread", "gap"), default="spread")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--spot-policy", choices=("match_nav", "krx"), default="match_nav")
    args = parser.parse_args()
    if args.mode == "gap":
        if not args.start or not args.end:
            parser.error("Gap mode requires --start and --end")
        with args.factor_log.open(encoding="utf-8-sig", newline="") as file:
            dates = tuple(row["date"] for row in csv.DictReader(file)
                          if args.start <= row["date"] <= args.end)
    else:
        dates = DATES
    if args.expected_count is not None and len(dates) != args.expected_count:
        raise ValueError(f"Expected {args.expected_count} dates, found {len(dates)}")
    if args.capture:
        if args.apply:
            parser.error("Cannot apply while capturing")
        atomic_write(args.capture, (json.dumps(capture(dates), ensure_ascii=False, indent=2) + "\n").encode())
        print(json.dumps({"captured_dates": len(dates), "snapshot": str(args.capture)}))
        return
    original, patched, report = plan(args.factor_log, args.nav,
                                     json.loads(args.snapshot.read_text(encoding="utf-8")),
                                     dates=dates, mode=args.mode,
                                     spot_policy=args.spot_policy)
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
