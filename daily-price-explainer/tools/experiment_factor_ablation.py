"""
팩터 카테고리별 개별 유의성 검증 (F1~F8 ablation).

experiment_unbiased_dl.py 와 **완전히 동일한** 검증 설계를 쓴다 — 확장윈도우
(앞 40% seed + 4개 OOS 블록), 동일 XGBoost 파라미터, 동일 누설 차단 7개 제외.
따라서 여기의 full_30 결과는 unbiased_dl_baseline.json 의 xgboost 수치와
일치해야 하며, 일치하지 않으면 데이터가 바뀐 것이다(회귀 테스트 역할).

두 방향으로 본다:
  · solo_Fk          — Fk 카테고리만 넣었을 때. "그 팩터 자체의 설명력"
  · leave_one_out_Fk — 30개에서 Fk 만 뺐을 때. "다른 팩터가 이미 설명한 부분을
                       제외한 Fk 의 한계 기여". 순서 의존성이 없어 solo 보다
                       해석이 안전하다.

유의성은 점추정(R²)이 아니라 블록 부트스트랩 95% 신뢰구간으로 판정한다.
divergence 는 자기상관(lag1~2 +0.13~0.17)이 있어 단순 부트스트랩은 신뢰구간을
과소추정하므로, residual_news_experiment 와 같은 20일 블록 / 2000회를 쓴다.

Usage:
  python tools/experiment_factor_ablation.py
  python tools/experiment_factor_ablation.py --output output/my_ablation.json
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from xgboost import XGBRegressor

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.features import FEATURE_TAXONOMY, FEATURES, build_features

DEFAULT_OUTPUT = BASE / "output" / "factor_ablation.json"

# experiment_unbiased_dl.py 와 동일 — 타깃과 같은 회계식에서 나온 값들
TARGET_DERIVED_FEATURES = [
    "divergence",
    "divergence_ma5",
    "nav_discount_pct",
    "nav_discount_delta",
    "nav_discount_vs_ma20",
    "nav_discount_z60",
    "skq_vol20",
]
CLEAN_FEATURES = [name for name in FEATURES if name not in TARGET_DERIVED_FEATURES]

BLOCK_LENGTH = 20
BOOTSTRAP_REPEATS = 2000

XGB_PARAMS = dict(
    n_estimators=300,
    learning_rate=0.03,
    max_depth=3,
    subsample=0.85,
    colsample_bytree=0.9,
    reg_lambda=2,
    min_child_weight=8,
    n_jobs=4,
    random_state=0,
    objective="reg:squarederror",
)


def _category_map() -> dict[str, list[str]]:
    """누설 차단 후 남은 피처를 F1~F8 최상위 코드로 묶는다."""
    groups: dict[str, list[str]] = {}
    for name in CLEAN_FEATURES:
        code = FEATURE_TAXONOMY[name][0].split(".")[0]
        groups.setdefault(code, []).append(name)
    return dict(sorted(groups.items()))


def _folds(n: int) -> list[tuple[int, int]]:
    """experiment_unbiased_dl.py 와 동일: 40% seed + 4개 테스트 블록."""
    edges = np.linspace(int(n * 0.4), n, 5).astype(int)
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def _load_dataset() -> pd.DataFrame:
    df = build_features()
    df[CLEAN_FEATURES] = df[CLEAN_FEATURES].ffill()
    return df.dropna(subset=["divergence"]).reset_index(drop=True)


def _oos_predict(df: pd.DataFrame, feats: list[str] | None) -> np.ndarray:
    """확장윈도우 OOS 예측. feats=None 이면 학습구간 평균 기준선."""
    y = df["divergence"].astype(float)
    n = len(y)
    pred = np.full(n, np.nan)

    for a, b in _folds(n):
        y_train = y.iloc[:a]
        if not feats:
            pred[a:b] = float(y_train.mean())
            continue
        imputer = SimpleImputer(strategy="median")
        x_train = imputer.fit_transform(df[feats].iloc[:a])
        x_test = imputer.transform(df[feats].iloc[a:b])
        model = XGBRegressor(**XGB_PARAMS)
        model.fit(x_train, y_train)
        pred[a:b] = model.predict(x_test)

    return pred


def _metrics(actual: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "r2": round(float(r2_score(actual, pred)), 4),
        "mae_pctp": round(float(mean_absolute_error(actual, pred)), 4),
        "sign_accuracy": round(float((np.sign(actual) == np.sign(pred)).mean()), 4),
    }


def _fold_r2(df: pd.DataFrame, pred: np.ndarray) -> list[float]:
    y = df["divergence"].astype(float).to_numpy()
    out = []
    for a, b in _folds(len(y)):
        out.append(round(float(r2_score(y[a:b], pred[a:b])), 4))
    return out


def _block_bootstrap_ci(diff: np.ndarray) -> tuple[float, float]:
    """이동블록 부트스트랩 95% CI. diff 는 일별 (기준오차 - 비교오차)."""
    rng = np.random.default_rng(0)
    n = len(diff)
    n_blocks = int(np.ceil(n / BLOCK_LENGTH))
    max_start = max(n - BLOCK_LENGTH, 0)
    starts = rng.integers(0, max_start + 1, size=(BOOTSTRAP_REPEATS, n_blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK_LENGTH)[None, None, :])
    idx = np.minimum(idx.reshape(BOOTSTRAP_REPEATS, -1)[:, :n], n - 1)
    means = diff[idx].mean(axis=1)
    return (
        round(float(np.percentile(means, 2.5)), 4),
        round(float(np.percentile(means, 97.5)), 4),
    )


def _compare(actual: np.ndarray, ref_pred: np.ndarray, test_pred: np.ndarray) -> dict:
    """test 가 ref 대비 MAE 를 얼마나 줄였는지 + 블록 부트스트랩 CI."""
    diff = np.abs(actual - ref_pred) - np.abs(actual - test_pred)  # 양수 = test 우세
    lo, hi = _block_bootstrap_ci(diff)
    return {
        "mae_improvement_pctp": round(float(diff.mean()), 4),
        "ci95_low": lo,
        "ci95_high": hi,
        "significant": bool(lo > 0),
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict:
    warnings.filterwarnings("ignore", category=UserWarning)
    df = _load_dataset()
    categories = _category_map()
    y = df["divergence"].astype(float)

    baseline_pred = _oos_predict(df, None)
    mask = ~np.isnan(baseline_pred)
    actual = y.to_numpy()[mask]

    full_pred = _oos_predict(df, CLEAN_FEATURES)

    solo: dict[str, dict] = {}
    for code, feats in categories.items():
        pred = _oos_predict(df, feats)
        solo[code] = {
            "features": feats,
            "n_features": len(feats),
            **_metrics(actual, pred[mask]),
            "folds_r2": _fold_r2(df, pred),
            "vs_baseline": _compare(actual, baseline_pred[mask], pred[mask]),
        }

    loo: dict[str, dict] = {}
    for code, feats in categories.items():
        remaining = [f for f in CLEAN_FEATURES if f not in feats]
        pred = _oos_predict(df, remaining)
        # 이 카테고리를 뺐을 때 성능이 나빠지면 = 기여가 있었다는 뜻
        loo[code] = {
            "n_features_remaining": len(remaining),
            **_metrics(actual, pred[mask]),
            "marginal_contribution": _compare(actual, pred[mask], full_pred[mask]),
        }

    result = {
        "experiment": "factor_category_ablation_v1",
        "as_of": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "target": "divergence",
        "validation": (
            "expanding window; first 40% seed; four OOS blocks; "
            f"moving-block bootstrap ({BLOCK_LENGTH}d blocks, {BOOTSTRAP_REPEATS} reps)"
        ),
        "methodology_note": (
            "experiment_unbiased_dl.py 와 동일 설계. full_30 이 그쪽 xgboost "
            "수치와 일치해야 정상이다."
        ),
        "n_total": int(len(y)),
        "n_oos": int(mask.sum()),
        "categories": {
            code: {"label": FEATURE_TAXONOMY[feats[0]][1].split("(")[0].strip(),
                   "features": feats}
            for code, feats in categories.items()
        },
        "baseline_mean": _metrics(actual, baseline_pred[mask]),
        "full_30": {
            "n_features": len(CLEAN_FEATURES),
            **_metrics(actual, full_pred[mask]),
            "folds_r2": _fold_r2(df, full_pred),
            "vs_baseline": _compare(actual, baseline_pred[mask], full_pred[mask]),
        },
        "solo": solo,
        "leave_one_out": loo,
        "interpretation_guardrails": [
            "solo 는 카테고리 자체 설명력, leave_one_out 은 다른 팩터를 통제한 한계 기여다.",
            "solo 가 유의해도 leave_one_out 이 유의하지 않으면, 그 정보는 다른 카테고리에 중복돼 있다.",
            "significant=true 는 MAE 개선의 95% CI 하한이 0을 넘는다는 뜻이며, 실무적 크기는 별개로 판단해야 한다.",
            "피처 수가 적은 카테고리(F1=2, F8=2)는 표본력이 낮아 유의성 미달이 신호 부재를 뜻하지 않는다.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def _print_summary(r: dict) -> None:
    print(f"\n{'='*78}")
    print(f"  팩터 카테고리별 유의성 — 타깃 divergence, OOS {r['n_oos']}일")
    print(f"{'='*78}")
    print(f"\n  기준선(평균)   R² {r['baseline_mean']['r2']:+.4f}   "
          f"MAE {r['baseline_mean']['mae_pctp']:.4f}%p   "
          f"부호 {r['baseline_mean']['sign_accuracy']:.3f}")
    f = r["full_30"]
    print(f"  전체 30개      R² {f['r2']:+.4f}   MAE {f['mae_pctp']:.4f}%p   "
          f"부호 {f['sign_accuracy']:.3f}   "
          f"{'유의' if f['vs_baseline']['significant'] else '미달'}")

    print(f"\n  ── solo (카테고리 단독) ──")
    print(f"  {'':5} {'n':>3}  {'R²':>8} {'MAE':>8} {'부호':>6}  "
          f"{'MAE개선':>8} {'95% CI':>18}  판정")
    for code, s in r["solo"].items():
        v = s["vs_baseline"]
        ci = f"[{v['ci95_low']:+.3f}, {v['ci95_high']:+.3f}]"
        print(f"  {code:5} {s['n_features']:>3}  {s['r2']:>+8.4f} "
              f"{s['mae_pctp']:>8.4f} {s['sign_accuracy']:>6.3f}  "
              f"{v['mae_improvement_pctp']:>+8.4f} {ci:>18}  "
              f"{'유의' if v['significant'] else '미달'}")

    print(f"\n  ── leave-one-out (한계 기여) ──")
    print(f"  {'':5} {'빼면 R²':>9}  {'한계기여':>8} {'95% CI':>18}  판정")
    for code, l in r["leave_one_out"].items():
        v = l["marginal_contribution"]
        ci = f"[{v['ci95_low']:+.3f}, {v['ci95_high']:+.3f}]"
        print(f"  {code:5} {l['r2']:>+9.4f}  {v['mae_improvement_pctp']:>+8.4f} "
              f"{ci:>18}  {'유의' if v['significant'] else '미달'}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.output)
    _print_summary(result)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
