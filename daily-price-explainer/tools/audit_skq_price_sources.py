"""Compare SK Square closes from KRX, pykrx default, and saved NAV data.

Only public market rows are written. KRX_KEY is used in an HTTPS header and
never included in the output. Run from the project root.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent


def audit(start: str, end: str) -> dict:
    import requests
    import truststore
    from dotenv import dotenv_values
    from pykrx import stock

    truststore.inject_into_ssl()
    key = dotenv_values(BASE / ".env").get("KRX_KEY")
    if not key:
        raise ValueError("KRX_KEY is absent")
    with (BASE / "data/nav_daily.csv").open(encoding="utf-8-sig", newline="") as file:
        saved = {r["date"]: r for r in csv.DictReader(file)
                 if start <= r["date"] <= end}
    with (BASE / "data/factor_log.csv").open(encoding="utf-8-sig", newline="") as file:
        factor = {r["date"]: r for r in csv.DictReader(file)
                  if start <= r["date"] <= end}
    if not saved or set(saved) != set(factor):
        raise ValueError("NAV/factor dates are absent or do not match")
    # The default pykrx path currently uses Naver adjusted historical closes.
    naver_frame = stock.get_market_ohlcv_by_date(start, end, "402340")
    naver = {d.strftime("%Y%m%d"): int(row["종가"])
             for d, row in naver_frame.iterrows()}
    session = requests.Session()
    session.headers["AUTH_KEY"] = key
    result = {}
    for date in sorted(saved):
        response = session.get(
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
            params={"basDd": date}, timeout=20,
        )
        response.raise_for_status()
        rows = response.json().get("OutBlock_1")
        if not isinstance(rows, list):
            raise ValueError(f"No KRX rows on {date}")
        matches = [r for r in rows if r.get("ISU_CD") == "402340"
                   and r.get("ISU_NM") == "SK스퀘어" and r.get("BAS_DD") == date]
        if len(matches) != 1:
            raise ValueError(f"SK Square stock row missing/duplicate on {date}")
        if date not in naver:
            raise ValueError(f"pykrx close missing on {date}")
        result[date] = {
            "krx_close": int(matches[0]["TDD_CLSPRC"].replace(",", "")),
            "krx_change": matches[0]["CMPPREVDD_PRC"],
            "krx_rate": matches[0]["FLUC_RT"],
            "pykrx_default_close": naver[date],
            "nav_daily_skq_close": float(saved[date]["skq_close"]),
            "factor_skq_ret": factor[date]["skq_ret"],
        }
    mismatch = [date for date, r in result.items()
                if len({r["krx_close"], r["pykrx_default_close"],
                        r["nav_daily_skq_close"]}) > 1]
    return {"range": [start, end], "source": {
        "krx": "KRX Open API /sto/stk_bydd_trd, ISU_CD=402340, AUTH_KEY via HTTPS header",
        "pykrx": "stock.get_market_ohlcv_by_date default adjusted=True",
        "saved": "data/nav_daily.csv and data/factor_log.csv working tree",
    }, "trading_days": len(result), "mismatch_days": mismatch, "rows": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260803")
    parser.add_argument("--end", default="20260929")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.start, args.end)
    # Keep output atomic and avoid committing the confidential key.
    try:
        from tools.repair_krx_factors import atomic_write
    except ModuleNotFoundError:
        from repair_krx_factors import atomic_write
    atomic_write(args.output, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"days": report["trading_days"],
                      "mismatch_days": len(report["mismatch_days"]),
                      "first_mismatch": report["mismatch_days"][0] if report["mismatch_days"] else None,
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
