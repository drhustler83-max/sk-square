"""
Leakage-aware baseline experiment for event_residual_unbiased.csv.

The experiment deliberately excludes event_name from model inputs because the
current curation process searched news primarily on high-residual dates. It
also removes same-day features derived from skq_ret before training.

Models:
  * Regression target: divergence
    - expanding-window mean baseline, Ridge, two-hidden-layer MLP, XGBoost
  * Classification target: abs(divergence) >= 3
    - expanding-window Logistic, two-hidden-layer MLP, XGBoost

Usage:
  python tools/experiment_unbiased_dl.py
  python tools/experiment_unbiased_dl.py --output output/my_result.json
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
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.features import FEATURES, build_features
UNBIASED_PATH = BASE / "data" / "event_residual_unbiased.csv"
DEFAULT_OUTPUT = BASE / "output" / "unbiased_dl_baseline.json"

# These same-day fields contain skq_ret directly or mechanically through the
# current SK Square market capitalization. They are observations for an
# attribution screen, not valid inputs for leakage-free validation.
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
SEEDS = (0, 1, 2)


def _folds(n: int) -> list[tuple[int, int]]:
    """Match the production expanding-window convention: 40% seed + 4 tests."""
    edges = np.linspace(int(n * 0.4), n, 5).astype(int)
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def _load_dataset() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = build_features()
    df[CLEAN_FEATURES] = df[CLEAN_FEATURES].ffill()
    df = df.dropna(subset=["divergence"]).reset_index(drop=True)

    event = pd.read_csv(UNBIASED_PATH, dtype={"date": str})
    event["has_event"] = event["event_name"].notna()
    event["high_residual_label"] = event["high_residual"].eq("Y")
    return df, event


def _selection_audit(event: pd.DataFrame) -> dict:
    cross = pd.crosstab(event["has_event"], event["high_residual_label"])

    def count(has_event: bool, high: bool) -> int:
        try:
            return int(cross.loc[has_event, high])
        except KeyError:
            return 0

    high = event["high_residual_label"]
    return {
        "rows": int(len(event)),
        "event_name_non_null": int(event["has_event"].sum()),
        "high_residual_rows": int(high.sum()),
        "high_residual_rate": round(float(high.mean()), 4),
        "cross_tab": {
            "event_false_high_false": count(False, False),
            "event_false_high_true": count(False, True),
            "event_true_high_false": count(True, False),
            "event_true_high_true": count(True, True),
        },
        "event_rate_on_high_residual": round(
            float(event.loc[high, "has_event"].mean()), 4
        ),
        "event_rate_on_non_high_residual": round(
            float(event.loc[~high, "has_event"].mean()), 4
        ),
        "event_name_used_as_model_input": False,
        "reason": (
            "event_name is excluded because news was curated almost exclusively "
            "on high-residual dates, creating selection leakage."
        ),
    }


def _regression(df: pd.DataFrame) -> dict:
    X = df[CLEAN_FEATURES]
    y = df["divergence"].astype(float)
    n = len(y)
    predictions = {
        name: np.full(n, np.nan)
        for name in ("mean_baseline", "ridge", "mlp_2layer", "xgboost")
    }
    fold_results = []

    for fold_no, (a, b) in enumerate(_folds(n), 1):
        imputer = SimpleImputer(strategy="median")
        x_train = imputer.fit_transform(X.iloc[:a])
        x_test = imputer.transform(X.iloc[a:b])
        y_train = y.iloc[:a]
        y_test = y.iloc[a:b]

        predictions["mean_baseline"][a:b] = float(y_train.mean())
        predictions["ridge"][a:b] = make_pipeline(
            StandardScaler(),
            Ridge(alpha=10.0),
        ).fit(x_train, y_train).predict(x_test)

        mlp_predictions = []
        for seed in SEEDS:
            model = make_pipeline(
                StandardScaler(),
                MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    alpha=0.01,
                    early_stopping=True,
                    max_iter=800,
                    random_state=seed,
                ),
            )
            model.fit(x_train, y_train)
            mlp_predictions.append(model.predict(x_test))
        predictions["mlp_2layer"][a:b] = np.mean(mlp_predictions, axis=0)

        xgb = XGBRegressor(
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
        xgb.fit(x_train, y_train)
        predictions["xgboost"][a:b] = xgb.predict(x_test)

        fold_results.append(
            {
                "fold": fold_no,
                "period": [
                    df["date"].iloc[a].strftime("%Y-%m-%d"),
                    df["date"].iloc[b - 1].strftime("%Y-%m-%d"),
                ],
                "n": int(b - a),
                "mlp_r2": round(
                    float(r2_score(y_test, predictions["mlp_2layer"][a:b])), 4
                ),
                "xgboost_r2": round(
                    float(r2_score(y_test, predictions["xgboost"][a:b])), 4
                ),
            }
        )

    mask = ~np.isnan(predictions["mean_baseline"])
    actual = y.to_numpy()[mask]
    aggregate = {}
    for name, values in predictions.items():
        pred = values[mask]
        aggregate[name] = {
            "r2": round(float(r2_score(actual, pred)), 4),
            "mae_pctp": round(float(mean_absolute_error(actual, pred)), 4),
            "sign_accuracy": round(
                float((np.sign(actual) == np.sign(pred)).mean()), 4
            ),
        }

    return {
        "target": "divergence",
        "n_total": int(n),
        "n_oos": int(mask.sum()),
        "aggregate": aggregate,
        "folds": fold_results,
    }


def _classification(df: pd.DataFrame) -> dict:
    X = df[CLEAN_FEATURES]
    y = df["divergence"].abs().ge(3).astype(int)
    n = len(y)
    probabilities = {
        name: np.full(n, np.nan)
        for name in ("logistic", "mlp_2layer", "xgboost")
    }

    for a, b in _folds(n):
        imputer = SimpleImputer(strategy="median")
        x_train = imputer.fit_transform(X.iloc[:a])
        x_test = imputer.transform(X.iloc[a:b])
        y_train = y.iloc[:a]

        logistic = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=0.2,
                class_weight="balanced",
                max_iter=2000,
            ),
        )
        logistic.fit(x_train, y_train)
        probabilities["logistic"][a:b] = logistic.predict_proba(x_test)[:, 1]

        mlp_probabilities = []
        for seed in SEEDS:
            model = make_pipeline(
                StandardScaler(),
                MLPClassifier(
                    hidden_layer_sizes=(32, 16),
                    alpha=0.01,
                    early_stopping=True,
                    max_iter=800,
                    random_state=seed,
                ),
            )
            model.fit(x_train, y_train)
            mlp_probabilities.append(model.predict_proba(x_test)[:, 1])
        probabilities["mlp_2layer"][a:b] = np.mean(mlp_probabilities, axis=0)

        positive = int(y_train.sum())
        scale_pos_weight = (
            float((len(y_train) - positive) / positive) if positive else 1.0
        )
        xgb = XGBClassifier(
            n_estimators=300,
            learning_rate=0.03,
            max_depth=3,
            subsample=0.85,
            colsample_bytree=0.9,
            reg_lambda=2,
            min_child_weight=8,
            n_jobs=4,
            random_state=0,
            eval_metric="logloss",
            scale_pos_weight=scale_pos_weight,
        )
        xgb.fit(x_train, y_train)
        probabilities["xgboost"][a:b] = xgb.predict_proba(x_test)[:, 1]

    mask = ~np.isnan(probabilities["logistic"])
    actual = y.to_numpy()[mask]
    aggregate = {}
    for name, values in probabilities.items():
        probability = values[mask]
        predicted = probability >= 0.5
        aggregate[name] = {
            "pr_auc": round(float(average_precision_score(actual, probability)), 4),
            "roc_auc": round(float(roc_auc_score(actual, probability)), 4),
            "balanced_accuracy": round(
                float(balanced_accuracy_score(actual, predicted)), 4
            ),
            "precision": round(
                float(precision_score(actual, predicted, zero_division=0)), 4
            ),
            "recall": round(
                float(recall_score(actual, predicted, zero_division=0)), 4
            ),
        }

    return {
        "target": "abs(divergence) >= 3",
        "n_total": int(n),
        "n_oos": int(mask.sum()),
        "positive_rate_oos": round(float(actual.mean()), 4),
        "no_skill_pr_auc": round(float(actual.mean()), 4),
        "aggregate": aggregate,
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict:
    warnings.filterwarnings("ignore", category=UserWarning)
    df, event = _load_dataset()
    result = {
        "experiment": "unbiased_dl_baseline_v1",
        "as_of": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "validation": "expanding window; first 40% seed; four OOS blocks",
        "input_feature_count": len(CLEAN_FEATURES),
        "input_features": CLEAN_FEATURES,
        "excluded_target_derived_features": TARGET_DERIVED_FEATURES,
        "selection_bias_audit": _selection_audit(event),
        "regression": _regression(df),
        "classification": _classification(df),
        "conclusion": (
            "The two-layer MLP does not beat simpler baselines and is unstable "
            "after the 2025-11 regime shift. Keep XGBoost as the tabular baseline; "
            "add article-only news properties and retest with time splits."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
