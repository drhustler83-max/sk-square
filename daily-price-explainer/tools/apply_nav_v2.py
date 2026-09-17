"""
data/nav_daily.csv 의 point-in-time NAV 를 factor_log.csv 에 반영 (v2).

기존 factor_log 의 NAV 컬럼은 company_context.py 의 고정 스냅샷(1Q26 기준,
발행주식수 132,087,115 고정)으로 전 기간을 계산한 값이라 과거가 부정확했다.
  · 발행주식수: 실제 141,467,571 → 131,923,998 (자사주 소각 -6.7%) 인데 고정
  · 비상장+현금: 실제 5.66조 → 3.85조 로 변동했는데 5.115조 고정

교체 컬럼 (원본은 *_v1 로 보존)
  nav_total_trillion / nav_implied_ret / divergence
  nav_discount_pct / nav_discount_delta

nav_implied_ret 산출 규칙 — 분기 계단 제거
  비상장·순현금은 분기 경계에서 계단으로 바뀌므로, 그대로 차분하면 분기 첫날마다
  가짜 수익률 스파이크가 생긴다. 분기 재평가는 '그날의 시장 움직임' 이 아니므로
  수익률은 시가평가 변동분만으로 계산한다.

      nav_prev_adj(t) = 상장가치(t-1) + 비상장(t) + 순현금(t)
      nav_implied_ret(t) = (NAV(t) / nav_prev_adj(t) - 1) x 100

  비교용으로 계단을 포함한 nav_implied_ret_raw 도 함께 남긴다.

Usage:
  python tools/apply_nav_v2.py            # 적용
  python tools/apply_nav_v2.py --dry-run  # 비교만 출력
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

FACTOR_LOG = BASE / "data" / "factor_log.csv"
NAV_DAILY = BASE / "data" / "nav_daily.csv"
BACKUP = BASE / "data" / "factor_log_v1_backup.csv"

REPLACED = ["nav_total_trillion", "nav_implied_ret", "divergence",
            "nav_discount_pct", "nav_discount_delta"]


def compute(nav: pd.DataFrame, skq_ret_ref: pd.Series) -> pd.DataFrame:
    """nav_daily → factor_log 용 NAV 컬럼.

    skq_ret 은 factor_log 에 수집 실패 구멍이 있으므로 nav_daily 의 종가에서
    직접 계산하고, factor_log 값은 대조용으로만 쓴다.
    """
    nav = nav.sort_values("date").reset_index(drop=True)

    close = nav["skq_close"].astype(float)
    skq_ret = ((close / close.shift(1) - 1.0) * 100.0).round(2)
    ref = pd.to_numeric(skq_ret_ref, errors="coerce")
    both = skq_ret.notna() & ref.notna()
    if both.any():
        gap = (skq_ret[both] - ref[both]).abs()
        logger.info(f"skq_ret 대조: 공통 {int(both.sum())}일, "
                    f"최대차 {gap.max():.3f}%p, 0.01 초과 {int((gap > 0.01).sum())}일")

    listed = nav["listed_value_mtm"].astype(float)
    unl = nav["unlisted_value"].astype(float)
    cash = nav["netcash_value"].astype(float)
    total = nav["nav_mtm"].astype(float)

    # 분기 계단 제거: 전일 상장가치 + 당일 비상장·순현금
    prev_adj = listed.shift(1) + unl + cash
    implied = (total / prev_adj - 1.0) * 100.0

    # 참고용: 계단 포함 원시 차분
    implied_raw = (total / total.shift(1) - 1.0) * 100.0

    disc = nav["nav_discount_pct_mtm"].astype(float)

    out = pd.DataFrame({
        "date": nav["date"].astype(str),
        "nav_total_trillion": (total / 1e12).round(2),
        "nav_implied_ret": implied.round(2),
        "nav_implied_ret_raw": implied_raw.round(2),
        "nav_discount_pct": disc.round(1),
        "nav_discount_delta": disc.diff().round(1),
        "nav_per_share": nav["nav_per_share_mtm"],
        "skq_shares_v2": nav["skq_shares"],
        "skq_ret_v2": skq_ret,
    })
    out["divergence"] = (skq_ret - out["nav_implied_ret"]).round(2)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    fl = pd.read_csv(FACTOR_LOG, dtype={"date": str})
    nav = pd.read_csv(NAV_DAILY, dtype={"date": str})
    logger.info(f"factor_log {len(fl)}행 / nav_daily {len(nav)}행")

    merged = nav.merge(fl[["date", "skq_ret"]], on="date", how="left")
    miss = int(merged["skq_ret"].isna().sum())
    if miss:
        logger.info(f"factor_log skq_ret 결측 {miss}일 → nav_daily 종가로 자체 계산")

    new = compute(merged, merged["skq_ret"])
    logger.info(f"v2 divergence 유효 {int(new['divergence'].notna().sum())}일 "
                f"/ {len(new)}일")

    # ── 비교 ──
    cmp_ = fl[["date"] + [c for c in REPLACED if c in fl.columns]].merge(
        new, on="date", how="inner", suffixes=("_v1", "_v2"))
    print(f"\n{'='*92}")
    print(f"  v1(기존 고정설정) vs v2(point-in-time) — 공통 {len(cmp_)}일")
    print(f"{'='*92}")
    for col in ["nav_total_trillion", "nav_implied_ret", "divergence", "nav_discount_pct"]:
        a_, b_ = f"{col}_v1", f"{col}_v2"
        if a_ not in cmp_ or b_ not in cmp_:
            continue
        d = (cmp_[b_].astype(float) - cmp_[a_].astype(float)).dropna()
        if d.empty:
            continue
        print(f"  {col:22} 평균차 {d.mean():>+9.3f}  절대평균 {d.abs().mean():>8.3f}  "
              f"최대 {d.abs().max():>9.3f}")

    print(f"\n  {'날짜':10} {'v1 할인율':>9} {'v2 할인율':>9}  {'v1 divg':>8} {'v2 divg':>8}")
    for dt in ["20211130", "20221229", "20231228", "20240628", "20250630", "20260630", "20260916"]:
        r = cmp_[cmp_.date == dt]
        if r.empty:
            continue
        r = r.iloc[0]
        print(f"  {dt:10} {float(r['nav_discount_pct_v1']):>9.1f} "
              f"{float(r['nav_discount_pct_v2']):>9.1f}  "
              f"{float(r['divergence_v1']):>8.2f} {float(r['divergence_v2']):>8.2f}")

    spike = (new["nav_implied_ret_raw"] - new["nav_implied_ret"]).abs()
    print(f"\n  분기 계단 제거 효과: 최대 {spike.max():.2f}%p, "
          f"0.1%p 초과한 날 {int((spike > 0.1).sum())}일")

    if a.dry_run:
        print("\n[dry-run] 파일을 쓰지 않았습니다.")
        return

    if not BACKUP.exists():
        shutil.copy2(FACTOR_LOG, BACKUP)
        logger.info(f"원본 백업: {BACKUP}")

    for c in REPLACED:
        if c in fl.columns and f"{c}_v1" not in fl.columns:
            fl[f"{c}_v1"] = fl[c]

    upd = new.set_index("date")
    idx = fl["date"]
    for c in REPLACED:
        fl[c] = idx.map(upd[c]).astype(object).where(idx.isin(upd.index), fl[c])
    for c in ["nav_implied_ret_raw", "nav_per_share", "skq_shares_v2"]:
        fl[c] = idx.map(upd[c])

    # skq_ret 수집 구멍을 종가 계산분으로 메움 (기존 값은 보존)
    if "skq_ret_v1" not in fl.columns:
        fl["skq_ret_v1"] = fl["skq_ret"]
    filled = idx.map(upd["skq_ret_v2"])
    n_fill = int(fl["skq_ret"].isna().sum() and filled.notna().sum())
    fl["skq_ret"] = pd.to_numeric(fl["skq_ret"], errors="coerce").fillna(filled)
    logger.info(f"skq_ret 결측 보완 후 유효 {int(fl['skq_ret'].notna().sum())}일")

    fl.to_csv(FACTOR_LOG, index=False, na_rep="")
    logger.info(f"적용 완료: {FACTOR_LOG}  ({len(fl)}행, {len(fl.columns)}컬럼)")
    print("\n원본 NAV 컬럼은 *_v1 로 보존했습니다. 되돌리려면 "
          f"{BACKUP.name} 을 복사하세요.")


if __name__ == "__main__":
    main()
