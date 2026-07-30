"""
Prepare event_residual_unbiased.csv for article-only case/control research.

Rules:
  * high residual case: abs(divergence) > mean(abs(divergence))
  * one unique low-residual control per case
  * controls are selected within the same calendar year
  * matching uses market-only state: KOSPI 20-day direction/volatility
  * event_name and all news fields are ignored during matching
  * event_residual_biased.csv is hashed before/after and must not change

Usage:
  python tools/prepare_unbiased_sampling.py          # dry run
  python tools/prepare_unbiased_sampling.py --apply  # update unbiased CSV
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.factor_logger import load_log


UNBIASED_PATH = BASE / "data" / "event_residual_unbiased.csv"
BIASED_PATH = BASE / "data" / "event_residual_biased.csv"
SAMPLING_COLUMNS = [
    "sampling_role",
    "matched_pair_id",
    "matched_date",
    "market_regime_20d",
    "market_volatility_bin",
    "match_quality",
    "match_distance",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _market_state() -> pd.DataFrame:
    factor = load_log().copy()
    factor["date"] = factor["date"].dt.strftime("%Y%m%d")
    ret = pd.to_numeric(factor["kospi_ret"], errors="coerce")

    gross = (1 + ret / 100).rolling(20, min_periods=5).apply(np.prod, raw=True)
    factor["market_return_20d"] = (gross - 1) * 100
    factor["market_vol20"] = ret.rolling(20, min_periods=5).std()
    factor["year"] = factor["date"].str[:4]

    # Early rows are filled with information available up to that date only.
    factor["market_return_20d"] = factor["market_return_20d"].fillna(
        ret.expanding(min_periods=1).sum()
    )
    factor["market_vol20"] = factor["market_vol20"].fillna(
        ret.expanding(min_periods=2).std()
    )
    factor["market_vol20"] = factor["market_vol20"].fillna(0.0)
    factor["market_regime_20d"] = np.where(
        factor["market_return_20d"] >= 0, "up", "down"
    )

    percentile = factor.groupby("year")["market_vol20"].rank(
        method="average", pct=True
    )
    factor["market_volatility_bin_num"] = np.select(
        [percentile <= 1 / 3, percentile <= 2 / 3],
        [0, 1],
        default=2,
    ).astype(int)
    factor["market_volatility_bin"] = factor[
        "market_volatility_bin_num"
    ].map({0: "low", 1: "mid", 2: "high"})

    for column in ("market_return_20d", "market_vol20"):
        mean = factor.groupby("year")[column].transform("mean")
        std = factor.groupby("year")[column].transform("std").replace(0, 1)
        factor[f"{column}_z"] = (factor[column] - mean) / std

    return factor[
        [
            "date",
            "year",
            "market_regime_20d",
            "market_volatility_bin",
            "market_volatility_bin_num",
            "market_return_20d_z",
            "market_vol20_z",
        ]
    ]


def prepare() -> tuple[pd.DataFrame, dict]:
    unbiased = pd.read_csv(UNBIASED_PATH, dtype=str).fillna("")
    # Make the operation idempotent when the prepared file is re-run.
    unbiased = unbiased.drop(columns=SAMPLING_COLUMNS, errors="ignore")
    base_columns = list(unbiased.columns)
    divergence = pd.to_numeric(unbiased["divergence"], errors="coerce")
    threshold = float(divergence.abs().mean())
    high = divergence.abs() > threshold

    unbiased["high_residual"] = np.where(high, "Y", "")

    frame = unbiased.merge(_market_state(), on="date", how="left")
    for column in (
        "sampling_role",
        "matched_pair_id",
        "matched_date",
        "match_quality",
        "match_distance",
    ):
        frame[column] = ""
    frame["year"] = frame["date"].str[:4]
    frame["date_dt"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    cases = frame.index[high].tolist()
    available = set(frame.index[(~high) & divergence.notna()].tolist())

    # Match difficult/high-volatility cases first while controls are plentiful.
    cases.sort(
        key=lambda i: (
            frame.at[i, "year"],
            -int(frame.at[i, "market_volatility_bin_num"]),
            frame.at[i, "date"],
        )
    )

    pairs: list[tuple[int, int, float, str]] = []
    for case_idx in cases:
        year = frame.at[case_idx, "year"]
        candidates = [i for i in available if frame.at[i, "year"] == year]
        if not candidates:
            raise RuntimeError(f"No unused same-year control for {frame.at[case_idx, 'date']}")

        case_regime = frame.at[case_idx, "market_regime_20d"]
        case_vol_bin = int(frame.at[case_idx, "market_volatility_bin_num"])

        def distance(control_idx: int) -> tuple[float, str]:
            regime_mismatch = int(
                frame.at[control_idx, "market_regime_20d"] != case_regime
            )
            vol_bin_gap = abs(
                int(frame.at[control_idx, "market_volatility_bin_num"])
                - case_vol_bin
            )
            state_distance = (
                float(
                    frame.at[control_idx, "market_return_20d_z"]
                    - frame.at[case_idx, "market_return_20d_z"]
                )
                ** 2
                + float(
                    frame.at[control_idx, "market_vol20_z"]
                    - frame.at[case_idx, "market_vol20_z"]
                )
                ** 2
            )
            day_gap = abs(
                (frame.at[control_idx, "date_dt"] - frame.at[case_idx, "date_dt"]).days
            )
            score = 5 * regime_mismatch + 2 * vol_bin_gap + state_distance + day_gap / 3650
            if regime_mismatch == 0 and vol_bin_gap == 0:
                quality = "exact_year_regime_vol"
            elif regime_mismatch == 0:
                quality = "same_year_regime"
            else:
                quality = "same_year"
            return score, quality

        selected = min(candidates, key=lambda i: distance(i)[0])
        score, quality = distance(selected)
        available.remove(selected)
        pairs.append((case_idx, selected, score, quality))

    frame["sampling_role"] = "unused_low"
    frame.loc[divergence.isna(), "sampling_role"] = "excluded_no_divergence"
    frame.loc[high, "sampling_role"] = "high_residual_case"

    for pair_no, (case_idx, control_idx, score, quality) in enumerate(pairs, 1):
        pair_id = f"M{pair_no:04d}"
        frame.at[case_idx, "matched_pair_id"] = pair_id
        frame.at[case_idx, "matched_date"] = frame.at[control_idx, "date"]
        frame.at[case_idx, "match_quality"] = quality
        frame.at[case_idx, "match_distance"] = f"{score:.6f}"

        frame.at[control_idx, "sampling_role"] = "matched_low_control"
        frame.at[control_idx, "matched_pair_id"] = pair_id
        frame.at[control_idx, "matched_date"] = frame.at[case_idx, "date"]
        frame.at[control_idx, "match_quality"] = quality
        frame.at[control_idx, "match_distance"] = f"{score:.6f}"

    # Copy market-state labels to both selected and unselected rows for auditability.
    for column in ("market_regime_20d", "market_volatility_bin"):
        frame[column] = frame[column].fillna("")

    output_columns = base_columns + SAMPLING_COLUMNS
    result = frame[output_columns].copy()
    roles = result["sampling_role"].value_counts().to_dict()
    quality = (
        result.loc[result["sampling_role"] == "high_residual_case", "match_quality"]
        .value_counts()
        .to_dict()
    )
    summary = {
        "rows": int(len(result)),
        "valid_divergence": int(divergence.notna().sum()),
        "mean_abs_divergence": threshold,
        "high_residual_cases": int(high.sum()),
        "roles": {str(k): int(v) for k, v in roles.items()},
        "case_match_quality": {str(k): int(v) for k, v in quality.items()},
        "controls_per_case": 1,
        "matching_with_replacement": False,
    }
    return result, summary


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig", lineterminator="\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    biased_before = _sha256(BIASED_PATH)
    prepared, summary = prepare()
    print(summary)

    if args.apply:
        _write_atomic(prepared, UNBIASED_PATH)
        print(f"Updated: {UNBIASED_PATH}")
    else:
        print("Dry run only. Re-run with --apply to update the unbiased CSV.")

    biased_after = _sha256(BIASED_PATH)
    if biased_before != biased_after:
        raise RuntimeError("event_residual_biased.csv changed unexpectedly")
    print(f"Biased file unchanged: {biased_after}")


if __name__ == "__main__":
    main()
