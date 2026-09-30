"""Capture selected KRX regular-session equity closes without storing credentials."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
TICKERS = {
    "402340": "sto/stk_bydd_trd",  # SK Square
    "000660": "sto/stk_bydd_trd",  # SK Hynix
    "259960": "sto/stk_bydd_trd",  # Krafton
    "060570": "sto/ksq_bydd_trd",  # Dreamus
    "216050": "sto/ksq_bydd_trd",  # Incross
    "039860": "sto/ksq_bydd_trd",  # NanoEntek
    "205500": "sto/ksq_bydd_trd",  # Nexus
}


def capture(start: str, end: str) -> dict:
    import requests
    import truststore
    from dotenv import dotenv_values

    truststore.inject_into_ssl()
    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    with (BASE / "data/factor_log.csv").open(encoding="utf-8-sig", newline="") as file:
        dates = [row["date"] for row in csv.DictReader(file)
                 if start <= row["date"] <= end]
    if not dates or len(dates) != len(set(dates)):
        raise ValueError("No dates or duplicate factor dates")
    session = requests.Session()
    session.headers["AUTH_KEY"] = key
    result = {}
    for date in dates:
        selected = {}
        for endpoint in sorted(set(TICKERS.values())):
            response = session.get(
                "https://data-dbg.krx.co.kr/svc/apis/" + endpoint,
                params={"basDd": date}, timeout=20,
            )
            response.raise_for_status()
            body = response.json()
            rows = body.get("OutBlock_1")
            if not isinstance(rows, list):
                raise ValueError(f"Missing KRX rows for {endpoint} {date}")
            for row in rows:
                ticker = row.get("ISU_CD")
                if TICKERS.get(ticker) == endpoint:
                    if ticker in selected or row.get("BAS_DD") != date:
                        raise ValueError(f"Duplicate/wrong-date row {ticker} {date}")
                    selected[ticker] = {field: row[field] for field in
                                        ("BAS_DD", "ISU_CD", "ISU_NM", "TDD_CLSPRC",
                                         "CMPPREVDD_PRC", "FLUC_RT", "ACC_TRDVOL")}
        if set(selected) != set(TICKERS):
            raise ValueError(f"Missing ticker rows on {date}: {set(TICKERS)-set(selected)}")
        result[date] = selected
    return {"source": "KRX Open API regular-session equity daily rows",
            "endpoints": sorted(set(TICKERS.values())), "tickers": TICKERS,
            "date_range": [start, end], "rows": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260915")
    parser.add_argument("--end", default="20260929")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = capture(args.start, args.end)
    try:
        from tools.repair_krx_factors import atomic_write
    except ModuleNotFoundError:
        from repair_krx_factors import atomic_write
    atomic_write(args.output,
                 (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"dates": len(report["rows"]), "tickers": len(TICKERS),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
