"""β − w 분해 재계산 (NAV v2 기준).

divergence 는 정의상 다음과 같이 분해된다.

    divergence(t) = skq_ret(t) − nav_implied_ret(t)

여기서 NAV 기대수익률은 보유자산 가중평균이므로

    nav_implied_ret(t) ≈ w_hx(t)·hynix_ret(t) + (그 외 자산 기여)

이고, 주가 자체는 하이닉스에 β 만큼 연동되므로

    skq_ret(t) ≈ β·hynix_ret(t) + ε(t)

따라서

    divergence(t) ≈ (β − w_hx(t))·hynix_ret(t) + …

즉 **β 와 w 의 격차가 divergence 의 하이닉스 민감도를 결정한다.**
β > w 이면 하이닉스가 오를 때 divergence 가 양(+)이 되고, β < w 이면 음(−)이 된다.

기존 진단은 company_context.py 의 고정 스냅샷 NAV(발행주식수·비상장가치 고정)로
w 를 계산했다. NAV v2 는 point-in-time 이라 w 의 궤적 자체가 달라지므로 재계산한다.

w 의 분모는 apply_nav_v2.py 의 nav_implied_ret 과 동일하게 맞춘다 (분기 계단 제거).

    w_hx(t) = 하이닉스가치(t-1) / (상장가치(t-1) + 비상장(t) + 순현금(t))

Usage:
  python tools/recompute_beta_w.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
FACTOR_LOG = BASE / "data" / "factor_log.csv"
NAV_DAILY = BASE / "data" / "nav_daily.csv"
OUT_JSON = BASE / "output" / "beta_w_decomposition.json"
OUT_CSV = BASE / "output" / "beta_w_daily.csv"

MIN_OBS = 30          # 연도별 회귀 최소 관측치
ROLL_WINDOW = 120     # 롤링 β 창


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float, float]:
    """단순회귀 y = a + b·x → (b, a, t_b, R²). 관측치 부족이면 NaN."""
    mask = np.isfinite(y) & np.isfinite(x)
    n = int(mask.sum())
    if n < 3:
        return (np.nan,) * 4
    yy, xx = y[mask], x[mask]
    xbar, ybar = xx.mean(), yy.mean()
    sxx = float(((xx - xbar) ** 2).sum())
    if sxx <= 0:
        return (np.nan,) * 4
    b = float(((xx - xbar) * (yy - ybar)).sum() / sxx)
    a = float(ybar - b * xbar)
    resid = yy - (a + b * xx)
    dof = n - 2
    if dof <= 0:
        return b, a, np.nan, np.nan
    s2 = float((resid ** 2).sum() / dof)
    se_b = float(np.sqrt(s2 / sxx)) if s2 > 0 else np.nan
    t_b = b / se_b if se_b and np.isfinite(se_b) and se_b > 0 else np.nan
    sst = float(((yy - ybar) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / sst if sst > 0 else np.nan
    return b, a, t_b, r2


def load() -> pd.DataFrame:
    fl = pd.read_csv(FACTOR_LOG, dtype={"date": str})
    nav = pd.read_csv(NAV_DAILY, dtype={"date": str})

    cols = ["date", "skq_ret", "hynix_ret", "divergence"]
    if "divergence_v1" in fl.columns:
        cols.append("divergence_v1")
    fl = fl[cols].copy()

    nav = nav.sort_values("date").reset_index(drop=True)
    listed_prev = nav["listed_value_mtm"].astype(float).shift(1)
    hx_prev = nav["hynix_value"].astype(float).shift(1)
    denom = listed_prev + nav["unlisted_value"].astype(float) + nav["netcash_value"].astype(float)

    nav_w = pd.DataFrame({
        "date": nav["date"].astype(str),
        # apply_nav_v2 의 nav_implied_ret 분모와 동일한 기준
        "w_hynix": (hx_prev / denom),
        # 참고: 당일 NAV 대비 하이닉스 비중 (해석용, 분모 기준 다름)
        "w_hynix_same_day": nav["hynix_value"].astype(float) / nav["nav_mtm"].astype(float),
        "nav_mtm_trillion": nav["nav_mtm"].astype(float) / 1e12,
    })

    df = fl.merge(nav_w, on="date", how="inner")
    for c in ["skq_ret", "hynix_ret", "divergence"] + (
            ["divergence_v1"] if "divergence_v1" in df.columns else []):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["year"] = df["date"].str[:4]
    return df.sort_values("date").reset_index(drop=True)


def main() -> None:
    df = load()
    print(f"\n관측 {len(df)}일  {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    ok = df[["skq_ret", "hynix_ret", "divergence"]].notna().all(axis=1)
    print(f"3개 변수 모두 유효한 날 {int(ok.sum())}일")

    has_v1 = "divergence_v1" in df.columns
    result: dict[str, object] = {
        "generated_from": {"factor_log": str(FACTOR_LOG.name), "nav_daily": str(NAV_DAILY.name)},
        "definition": "divergence ≈ (β − w_hynix) × hynix_ret",
        "w_denominator": "listed_value_mtm(t-1) + unlisted_value(t) + netcash_value(t)",
        "observations": int(len(df)),
        "date_start": df["date"].iloc[0],
        "date_end": df["date"].iloc[-1],
    }

    # ── 전 기간 ──
    b_all, _, t_all, r2_all = _ols(df["skq_ret"].to_numpy(), df["hynix_ret"].to_numpy())
    s_all, _, ts_all, rs_all = _ols(df["divergence"].to_numpy(), df["hynix_ret"].to_numpy())
    w_all = float(df["w_hynix"].mean())
    result["full_sample"] = {
        "beta": round(b_all, 4), "beta_t": round(t_all, 2), "beta_r2": round(r2_all, 4),
        "w_hynix_mean": round(w_all, 4),
        "beta_minus_w": round(b_all - w_all, 4),
        "divergence_on_hynix_slope": round(s_all, 4),
        "divergence_on_hynix_t": round(ts_all, 2),
        "divergence_on_hynix_r2": round(rs_all, 4),
        "corr_divergence_hynix": round(float(df[["divergence", "hynix_ret"]].corr().iloc[0, 1]), 4),
    }
    if has_v1:
        s1, _, ts1, _ = _ols(df["divergence_v1"].to_numpy(), df["hynix_ret"].to_numpy())
        result["full_sample"]["v1_divergence_on_hynix_slope"] = round(s1, 4)
        result["full_sample"]["v1_divergence_on_hynix_t"] = round(ts1, 2)

    print(f"\n{'='*94}")
    print("  전 기간")
    print(f"{'='*94}")
    print(f"  β (skq_ret ~ hynix_ret)        {b_all:+.4f}  (t={t_all:.1f}, R²={r2_all:.3f})")
    print(f"  w̄ (하이닉스 NAV 비중 평균)      {w_all:+.4f}")
    print(f"  β − w̄                          {b_all - w_all:+.4f}")
    print(f"  실측 기울기 (divergence~hynix) {s_all:+.4f}  (t={ts_all:.1f}, R²={rs_all:.3f})")
    if has_v1:
        print(f"    └ v1 기준 실측 기울기         {s1:+.4f}  (t={ts1:.1f})")

    # ── 연도별 ──
    rows = []
    print(f"\n{'='*94}")
    print("  연도별 — β 와 w 의 격차가 divergence 의 하이닉스 민감도를 만든다")
    print(f"{'='*94}")
    head = (f"  {'연도':<6}{'n':>5}{'β':>9}{'(t)':>8}{'w̄':>9}{'β−w̄':>10}"
            f"{'실측기울기':>12}{'(t)':>8}{'상관':>8}")
    print(head)
    print(f"  {'-'*88}")
    for year, g in df.groupby("year"):
        y = g["divergence"].to_numpy()
        x = g["hynix_ret"].to_numpy()
        n = int((np.isfinite(y) & np.isfinite(x)).sum())
        if n < MIN_OBS:
            continue
        beta, _, tb, _ = _ols(g["skq_ret"].to_numpy(), x)
        slope, _, tslope, r2s = _ols(y, x)
        w = float(g["w_hynix"].mean())
        corr = float(g[["divergence", "hynix_ret"]].corr().iloc[0, 1])
        row = {
            "year": year, "n": n,
            "beta": round(beta, 4), "beta_t": round(tb, 2),
            "w_hynix_mean": round(w, 4),
            "w_hynix_min": round(float(g["w_hynix"].min()), 4),
            "w_hynix_max": round(float(g["w_hynix"].max()), 4),
            "beta_minus_w": round(beta - w, 4),
            "divergence_on_hynix_slope": round(slope, 4),
            "divergence_on_hynix_t": round(tslope, 2),
            "divergence_on_hynix_r2": round(r2s, 4),
            "corr_divergence_hynix": round(corr, 4),
        }
        if has_v1:
            s1y, _, t1y, _ = _ols(g["divergence_v1"].to_numpy(), x)
            row["v1_divergence_on_hynix_slope"] = round(s1y, 4)
            row["v1_divergence_on_hynix_t"] = round(t1y, 2)
        rows.append(row)
        print(f"  {year:<6}{n:>5}{beta:>+9.3f}{tb:>8.1f}{w:>+9.3f}{beta - w:>+10.3f}"
              f"{slope:>+12.3f}{tslope:>8.1f}{corr:>+8.3f}")
    result["by_year"] = rows

    # 부호 반전 여부
    signs = {r["year"]: int(np.sign(r["beta_minus_w"])) for r in rows}
    flipped = len(set(signs.values())) > 1
    result["sign_flip_detected"] = bool(flipped)
    result["beta_minus_w_sign_by_year"] = signs
    print(f"\n  β−w̄ 부호: " + "  ".join(f"{y}:{'+' if s > 0 else '−' if s < 0 else '0'}"
                                        for y, s in signs.items()))
    print(f"  부호 반전 {'있음' if flipped else '없음'}")

    if has_v1:
        print(f"\n  [v1 대조] 연도별 실측 기울기")
        print(f"  {'연도':<6}{'v1':>10}{'v2':>10}{'차이':>10}")
        for r in rows:
            d = r["divergence_on_hynix_slope"] - r["v1_divergence_on_hynix_slope"]
            print(f"  {r['year']:<6}{r['v1_divergence_on_hynix_slope']:>+10.3f}"
                  f"{r['divergence_on_hynix_slope']:>+10.3f}{d:>+10.3f}")

    # ── 롤링 ──
    roll_beta, roll_slope = [], []
    sr, hr, dv = df["skq_ret"].to_numpy(), df["hynix_ret"].to_numpy(), df["divergence"].to_numpy()
    for i in range(len(df)):
        if i + 1 < ROLL_WINDOW:
            roll_beta.append(np.nan)
            roll_slope.append(np.nan)
            continue
        sl = slice(i + 1 - ROLL_WINDOW, i + 1)
        roll_beta.append(_ols(sr[sl], hr[sl])[0])
        roll_slope.append(_ols(dv[sl], hr[sl])[0])
    df["beta_roll120"] = roll_beta
    df["divergence_slope_roll120"] = roll_slope
    df["beta_minus_w_roll120"] = df["beta_roll120"] - df["w_hynix"]

    valid = df["beta_minus_w_roll120"].notna() & df["divergence_slope_roll120"].notna()
    if int(valid.sum()) > 10:
        corr_track = float(df.loc[valid, ["beta_minus_w_roll120",
                                          "divergence_slope_roll120"]].corr().iloc[0, 1])
        result["rolling"] = {
            "window": ROLL_WINDOW,
            "n": int(valid.sum()),
            "corr_beta_minus_w_vs_realized_slope": round(corr_track, 4),
        }
        print(f"\n  롤링 {ROLL_WINDOW}일: (β−w) 와 실측 기울기의 상관 {corr_track:+.4f}"
              f"  (n={int(valid.sum())})")
        print("  → 1에 가까울수록 β−w 메커니즘이 divergence 를 실제로 설명한다는 뜻")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    df[["date", "year", "skq_ret", "hynix_ret", "divergence", "w_hynix",
        "beta_roll120", "beta_minus_w_roll120", "divergence_slope_roll120"]].to_csv(
        OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT_JSON}")
    print(f"저장: {OUT_CSV}")


if __name__ == "__main__":
    main()
