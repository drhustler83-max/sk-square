"""
SK스퀘어 일별 NAV 산출 → data/nav_daily.csv

구성
  NAV = 상장사 지분가치 + 직전분기 비상장사 지분가치 + 직전분기 순현금

  · 상장분   tools/build_listed_holdings.py 가 만든 data/listed_holdings_daily.csv
  · 비상장   분기말 합계 (Sean 제공, 억원). 9개 비상장사 합계이며 드림어스·넥써쓰·
             아이온큐는 포함하지 않는다(그쪽은 상장분에서 처리).
  · 순현금   분기말 (Sean 제공, 억원)

직전분기(lag) 규칙
  비상장·순현금은 분기 결산 후에야 알 수 있으므로, 어떤 날짜 t 의 NAV 에는
  t 가 속한 분기의 '직전분기' 값을 쓴다 (--lag 로 조정, 기본 1).
  2021Q4(2021-11-30~12-31)는 직전분기가 없어 해당 분기 값을 그대로 쓰고
  nav_basis='stub_as_of' 로 표시한다.

두 가지 NAV
  nav_mtm        모든 상장 보유분을 매일 시가평가. 경제적으로 정확
  nav_company    SK하이닉스만 매일 시가평가하고, 나머지 상장 보유분은
                 직전분기말 가치로 고정. 회사 IR 관행에 맞춘 버전
  두 값의 차이는 드림어스·인크로스·나노엔텍·크래프톤·넥써쓰·IONQ 의
  분기 중 주가 변동분이다.

Usage:
  python tools/build_nav_daily.py
  python tools/build_nav_daily.py --lag 0      # 직전분기 대신 당분기 값 사용
"""

from __future__ import annotations

import argparse
import ssl
import sys
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

import numpy as np
import pandas as pd
from pykrx import stock
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

LISTED_CSV = BASE / "data" / "listed_holdings_daily.csv"
DEFAULT_OUTPUT = BASE / "data" / "nav_daily.csv"
SKQ = "402340"

EOK = 1e8  # 1억원

# 분기말 비상장사 지분가치 합계 (억원) — Sean 제공
UNLISTED_EOK: dict[tuple[int, int], float] = {
    (2020, 4): 56_570,
    (2021, 4): 64_873,
    (2022, 1): 66_039, (2022, 2): 66_353, (2022, 3): 72_286, (2022, 4): 69_079,
    (2023, 1): 72_579, (2023, 2): 72_515, (2023, 3): 69_512, (2023, 4): 55_995,
    (2024, 1): 56_116, (2024, 2): 56_271, (2024, 3): 54_464, (2024, 4): 49_602,
    (2025, 1): 51_265, (2025, 2): 44_808, (2025, 3): 41_543, (2025, 4): 40_935,
    (2026, 1): 42_642, (2026, 2): 38_526,
}

# 분기말 순현금 (억원) — Sean 제공
NETCASH_EOK: dict[tuple[int, int], float] = {
    (2021, 4): -102.64,
    (2022, 1): -1_080.93, (2022, 2): 1_482.89, (2022, 3): 1_779.45, (2022, 4): 1_880,
    (2023, 1): 492, (2023, 2): 1_131, (2023, 3): 4_286, (2023, 4): 4_577,
    (2024, 1): 4_288, (2024, 2): 6_306, (2024, 3): 6_648, (2024, 4): 5_362,
    (2025, 1): 4_316, (2025, 2): 10_753, (2025, 3): 10_882, (2025, 4): 7_690,
    (2026, 1): 7_937, (2026, 2): 8_012,
}

LISTED_KEYS = ["skhynix", "dreamus", "incross", "nanoentek", "krafton", "nexus", "ionq"]
OTHER_KEYS = [k for k in LISTED_KEYS if k != "skhynix"]

# SK스퀘어 발행주식총수 (자기주식 포함) — 자사주 소각일 기준 정확 계단
#   출처: Sean 제공 소각 이력. DART stockTotqySttus(istc_totqy) 정기보고서
#   스냅샷 10개와 전부 교차검증 일치했다.
#     2021-12-31 / 2022-06-30 / 2022-12-31 / 2023-06-30  → 141,467,571 ✓
#     2023-12-31 → 138,981,036 ✓ (2023-10-04 소각 반영)
#     2024-06-30 / 2024-12-31 → 134,749,960 ✓ (2024-04-02 소각 반영)
#     2025-06-30 → 132,540,858 ✓ (2025-01-06·04-01 소각 반영)
#     2025-12-31 → 132,087,115 ✓ (2025-11-24 소각 반영)
#     2026-06-30 → 131,958,386 ✓ (2026-04-01 소각 반영)
#   자사주 소각 누적 9,543,573주 (-6.7%)
SKQ_SHARES: list[tuple[str, int, str]] = [
    ("20211130", 141_467_571, "분할상장 시점"),
    ("20231004", 138_981_036, "2023-10-04 소각 2,486,535주"),
    ("20240402", 134_749_960, "2024-04-02 소각 4,231,076주"),
    ("20250106", 133_548_056, "2025-01-06 소각 1,201,904주"),
    ("20250401", 132_540_858, "2025-04-01 소각 1,007,198주"),
    ("20251124", 132_087_115, "2025-11-24 소각 453,743주"),
    ("20260401", 131_958_386, "2026-04-01 소각 128,729주"),
    ("20260731", 131_923_998, "2026-07-31 소각 34,388주"),
]


def _q(ts: pd.Timestamp) -> tuple[int, int]:
    return (int(ts.year), int((ts.month - 1) // 3 + 1))


def _shift_q(q: tuple[int, int], back: int) -> tuple[int, int]:
    y, n = q
    idx = y * 4 + (n - 1) - back
    return (idx // 4, idx % 4 + 1)


def _lookup(table: dict, q: tuple[int, int]) -> tuple[float | None, tuple[int, int] | None]:
    """q 이하에서 가장 최근 값. (값, 사용된 분기)"""
    keys = sorted(k for k in table if k <= q)
    if not keys:
        return None, None
    k = keys[-1]
    return float(table[k]), k


def _skq_shares(index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """SK스퀘어 발행주식총수 일별 (DART 기준 계단함수) + 근거 시리즈.

    pykrx get_market_cap 은 KRX 인증이 필요해 현재 막혀 있으므로 DART 를 쓴다.
    """
    sh = pd.Series(np.nan, index=index, dtype="float64")
    why = pd.Series("", index=index, dtype=object)
    for start, qty, note in SKQ_SHARES:
        mask = index >= pd.Timestamp(start)
        sh.loc[mask] = float(qty)
        why.loc[mask] = note
    n_uniq = sh.nunique()
    logger.info(f"  발행주식총수 {n_uniq}개 구간: "
                f"{sh.iloc[0]:,.0f} → {sh.iloc[-1]:,.0f}")
    return sh, why


def build(output: Path = DEFAULT_OUTPUT, lag: int = 1) -> pd.DataFrame:
    if not LISTED_CSV.exists():
        raise FileNotFoundError(f"{LISTED_CSV} 없음 — build_listed_holdings.py 를 먼저 실행")

    lf = pd.read_csv(LISTED_CSV, dtype={"date": str})
    lf["dt"] = pd.to_datetime(lf["date"], format="%Y%m%d")
    lf = lf.sort_values("dt").reset_index(drop=True)
    idx = pd.DatetimeIndex(lf["dt"])
    logger.info(f"상장분 {len(lf)}행  {lf['date'].iloc[0]} ~ {lf['date'].iloc[-1]}")

    out = pd.DataFrame({"date": lf["date"]})
    out["hynix_value"] = lf["skhynix_value"]
    out["other_listed_value_mtm"] = lf[[f"{k}_value" for k in OTHER_KEYS]].sum(axis=1)
    out["listed_value_mtm"] = out["hynix_value"] + out["other_listed_value_mtm"]

    # ── 분기 귀속 ──
    qs = [_q(t) for t in idx]
    src_q = [_shift_q(q, lag) for q in qs]

    unl, cash, basis, unl_q, cash_q = [], [], [], [], []
    for q, sq in zip(qs, src_q):
        u, uk = _lookup(UNLISTED_EOK, sq)
        c, ck = _lookup(NETCASH_EOK, sq)
        b = "lagged"
        if u is None or c is None:            # 직전분기 없음 → 당분기 사용
            u2, uk2 = _lookup(UNLISTED_EOK, q)
            c2, ck2 = _lookup(NETCASH_EOK, q)
            u, uk = (u if u is not None else u2), (uk if uk is not None else uk2)
            c, ck = (c if c is not None else c2), (ck if ck is not None else ck2)
            b = "stub_as_of"
        unl.append(u * EOK if u is not None else np.nan)
        cash.append(c * EOK if c is not None else np.nan)
        basis.append(b)
        unl_q.append(f"{uk[0]}Q{uk[1]}" if uk else "")
        cash_q.append(f"{ck[0]}Q{ck[1]}" if ck else "")

    out["unlisted_value"] = unl
    out["netcash_value"] = cash
    out["unlisted_src_q"] = unl_q
    out["netcash_src_q"] = cash_q
    out["nav_basis"] = basis

    # ── 회사 관행: 하이닉스 외 상장분을 직전분기말 값으로 고정 ──
    qend_val: dict[tuple[int, int], float] = {}
    tmp = pd.DataFrame({"q": qs, "v": out["other_listed_value_mtm"].to_numpy()})
    for q, g in tmp.groupby("q", sort=False):
        qend_val[q] = float(g["v"].iloc[-1])

    frozen = []
    for q, sq in zip(qs, src_q):
        v = qend_val.get(sq)
        if v is None:                          # 직전분기 데이터 없음 → 당일 시가
            v = float(out["other_listed_value_mtm"].iloc[len(frozen)])
        frozen.append(v)
    out["other_listed_value_frozen"] = frozen

    out["nav_mtm"] = out["listed_value_mtm"] + out["unlisted_value"] + out["netcash_value"]
    out["nav_company"] = (out["hynix_value"] + out["other_listed_value_frozen"]
                          + out["unlisted_value"] + out["netcash_value"])

    # ── SK스퀘어 시총·주당NAV·할인율 ──
    logger.info("SK스퀘어 발행주식총수 (DART)")
    sh_ser, sh_why = _skq_shares(idx)
    shares = sh_ser.to_numpy()
    px = stock.get_market_ohlcv(lf["date"].iloc[0], lf["date"].iloc[-1], SKQ)["종가"].astype(float)
    px.index = pd.to_datetime(px.index)
    px = px.reindex(idx).ffill().to_numpy()

    out["skq_close"] = px
    out["skq_shares"] = shares.astype("int64")
    out["skq_shares_basis"] = sh_why.to_numpy()
    out["skq_market_cap"] = px * shares

    for tag in ("mtm", "company"):
        nav = out[f"nav_{tag}"].to_numpy()
        nps = np.where(shares > 0, nav / shares, np.nan)
        out[f"nav_per_share_{tag}"] = np.round(nps, 0)
        out[f"nav_discount_pct_{tag}"] = np.round(
            np.where((nav > 0) & (shares > 0), (1 - px / nps) * 100, np.nan), 2)

    for c in ["hynix_value", "other_listed_value_mtm", "listed_value_mtm",
              "other_listed_value_frozen", "unlisted_value", "netcash_value",
              "nav_mtm", "nav_company", "skq_market_cap"]:
        out[c] = out[c].round(0)
    for tag in ("mtm", "company"):
        out[f"nav_{tag}_trillion"] = (out[f"nav_{tag}"] / 1e12).round(4)

    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False, encoding="utf-8-sig")
    logger.info(f"저장: {output}  ({len(out)}행)")
    return out


def _summary(df: pd.DataFrame, lag: int) -> None:
    print(f"\n{'='*112}")
    print(f"  SK스퀘어 일별 NAV — {len(df)}거래일 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})"
          f"   비상장·순현금 lag={lag}분기")
    print(f"{'='*112}")
    d = df.copy()
    d["q"] = d["date"].str[:4] + "Q" + ((d["date"].str[4:6].astype(int) - 1) // 3 + 1).astype(str)
    print(f"\n  {'분기':8} {'마지막일':10} {'하이닉스':>10} {'기타상장':>9} {'비상장':>9} "
          f"{'순현금':>8} {'NAV(조)':>9} {'주당NAV':>11} {'주가':>10} {'할인율%':>8}  출처")
    for q, g in d.groupby("q"):
        r = g.iloc[-1]
        print(f"  {q:8} {r['date']:10} {r['hynix_value']/1e12:>10.2f} "
              f"{r['other_listed_value_mtm']/1e12:>9.3f} {r['unlisted_value']/1e12:>9.3f} "
              f"{r['netcash_value']/1e12:>8.3f} {r['nav_mtm_trillion']:>9.3f} "
              f"{r['nav_per_share_mtm']:>11,.0f} {r['skq_close']:>10,.0f} "
              f"{r['nav_discount_pct_mtm']:>8.1f}  {r['unlisted_src_q']}/{r['netcash_src_q']}")

    diff = (df["nav_mtm"] - df["nav_company"]).abs() / df["nav_mtm"] * 100
    print(f"\n  nav_mtm vs nav_company 차이: 평균 {diff.mean():.3f}%p, 최대 {diff.max():.3f}%p")
    stub = int((df["nav_basis"] == "stub_as_of").sum())
    if stub:
        print(f"  ※ 직전분기 부재로 당분기 값 사용: {stub}일 (nav_basis=stub_as_of)")
    na = int(df["nav_per_share_mtm"].isna().sum())
    if na:
        print(f"  ※ 상장주식수 미확보로 주당NAV 결측: {na}일")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--lag", type=int, default=1, help="비상장·순현금 분기 지연 (기본 1)")
    a = p.parse_args()
    df = build(a.output, a.lag)
    _summary(df, a.lag)
    print(f"Saved: {a.output}")


if __name__ == "__main__":
    main()
