"""
factor_log.csv 완전성 감사 — 거래일 누락·컬럼 결측을 분류해 보고.

복구 작업(수급·공매도·선물·매크로 백필) 전후로 재실행해 무엇이 남았는지
객관적으로 확인하는 용도. 결측을 3부류로 나눈다:
  (A) 최근 갭   — RECENT_CUTOFF(기본 20260917) 이후. 진행 중 복구 대상
  (B) 과거 결측 — 그 이전의 진짜 결측(재수집/재계산 대상)
  (C) 정상 결측 — 복구 불필요(첫 거래일 파생값, 선물 미상장, *_v1 신규구간)

사용:
  python tools/audit_factor_log.py                 # 사람이 읽는 리포트
  python tools/audit_factor_log.py --json out.json # 기계용 JSON 병기
  python tools/audit_factor_log.py --start 20211129 --end 20260929

주의: 거래일 목록을 pykrx(네이버 경로)로 받으므로 네트워크 필요. 사내망
SSL 우회를 위해 verify=False 를 주입한다(다른 tool 들과 동일).
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *a, **k):
    k.setdefault("verify", False)
    return _orig(self, *a, **k)
requests.Session.request = _no_verify

import pandas as pd
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

FACTOR_LOG = BASE / "data" / "factor_log.csv"
LISTED_CSV = BASE / "data" / "listed_holdings_daily.csv"

# *_v1 컬럼은 2026-09-17 NAV v1→v2 마이그레이션 당시 원본 보존용. 그 이후
# 날짜엔 원래 존재하지 않으므로 신규구간 결측은 정상(C).
V1_COLS = ["nav_total_trillion_v1", "nav_implied_ret_v1", "divergence_v1",
           "nav_discount_pct_v1", "nav_discount_delta_v1", "skq_ret_v1"]

DEFAULT_START = "20211129"
DEFAULT_END = "20260929"
RECENT_CUTOFF = "20260917"  # 이 날짜 이후 결측은 (A) 최근 갭


def _empty(s: pd.Series) -> pd.Series:
    return s.isna() | (s.astype(str).str.strip() == "")


def _trading_days(start: str, end: str, ticker: str = "402340") -> list[str]:
    from pykrx import stock
    idx = stock.get_market_ohlcv(start, end, ticker).index
    return [d.strftime("%Y%m%d") for d in idx if start <= d.strftime("%Y%m%d") <= end]


def audit(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    df = pd.read_csv(FACTOR_LOG, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    have = set(df["date"])
    tdays = _trading_days(start, end)

    missing_rows = sorted(d for d in tdays if d not in have)
    extra_rows = sorted(d for d in have if d not in set(tdays))

    # 선물 미상장(fut_listed=0)은 정상 결측
    fl = pd.to_numeric(df.get("fut_listed"), errors="coerce") if "fut_listed" in df else None

    hynix_recoverable = _hynix_recoverable_dates(df)

    cols = {}
    for c in df.columns:
        if c == "date":
            continue
        m = _empty(df[c])
        if not m.any():
            continue
        dates = list(df.loc[m, "date"])
        recent = [d for d in dates if d >= RECENT_CUTOFF]
        past = [d for d in dates if d < RECENT_CUTOFF]

        # 정상(C) 판정
        normal = []
        if start in dates:                       # 첫 거래일 파생값
            normal.append(start)
        if c in V1_COLS:                          # v1 = 09-17 마이그레이션 동결 스냅샷
            normal += dates                      # → 결측 전부 정상(복구 대상 아님)
        if c in ("fut_basis", "fut_basis_pct", "fut_volume") and fl is not None:
            normal += list(df.loc[m & (fl == 0), "date"])  # 선물 미상장
        normal = sorted(set(normal))

        true_missing = sorted(set(dates) - set(normal))
        cols[c] = {
            "total_missing": len(dates),
            "recent_gap": sorted(set(recent) - set(normal)),      # (A)
            "past_true_missing": sorted(set(past) - set(normal)),  # (B)
            "normal": normal,                                      # (C)
        }

    return {
        "range": [start, end],
        "rows_expected": len(tdays),
        "rows_present": len(have),
        "missing_rows": missing_rows,
        "extra_rows": extra_rows,
        "hynix_ret_recomputable_dates": hynix_recoverable,
        "columns": cols,
    }


def _hynix_recoverable_dates(df: pd.DataFrame) -> list[str]:
    """hynix_ret 결측 중 listed_holdings 의 하이닉스 종가로 재계산 가능한 날."""
    if not LISTED_CSV.exists() or "hynix_ret" not in df:
        return []
    lh = pd.read_csv(LISTED_CSV, dtype={"date": str})
    if "skhynix_price" not in lh:
        return []
    ok = set(lh.loc[~_empty(lh["skhynix_price"]), "date"])
    miss = df.loc[_empty(df["hynix_ret"]), "date"]
    return sorted(d for d in miss if d in ok)


def _print(rep: dict) -> None:
    print(f"\n{'='*70}\n  factor_log.csv 완전성 감사 — {rep['range'][0]} ~ {rep['range'][1]}\n{'='*70}")
    print(f"거래일 {rep['rows_expected']}개 / factor_log {rep['rows_present']}행 "
          f"| 누락행 {len(rep['missing_rows'])} | 초과행 {len(rep['extra_rows'])}")
    if rep["missing_rows"]:
        print("  ⚠ 누락된 거래일:", rep["missing_rows"][:30])

    print(f"\n[A] 최근 갭 (>= {RECENT_CUTOFF}) — 진행 중 복구 대상")
    for c, v in rep["columns"].items():
        if v["recent_gap"]:
            print(f"   {c:24} {len(v['recent_gap'])}일")

    print("\n[B] 과거 진짜 결측 — 재수집/재계산 대상")
    for c, v in rep["columns"].items():
        if v["past_true_missing"]:
            ds = v["past_true_missing"]
            tag = ""
            if c == "hynix_ret":
                tag = f" (listed_holdings로 {len(rep['hynix_ret_recomputable_dates'])}일 재계산 가능)"
            print(f"   {c:24} {len(ds)}일  {ds if len(ds)<=8 else ds[:8]+['...']}{tag}")

    print("\n[C] 정상 결측 — 복구 불필요")
    for c, v in rep["columns"].items():
        if v["normal"]:
            print(f"   {c:24} {len(v['normal'])}일 (첫거래일/선물미상장/v1신규구간)")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--json", type=Path, default=None)
    a = p.parse_args()
    rep = audit(a.start, a.end)
    _print(rep)
    if a.json:
        a.json.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 저장: {a.json}")


if __name__ == "__main__":
    main()
