"""Leakage-aware numeric + news experiment for SK Square divergence.

The news workbook is a matched case/control research sample, not a full-market
panel.  This script therefore evaluates news only on dates that were searched
under the same protocol and keeps ``sampling_role`` out of every model input.

Usage:
    python -m tools.experiment_residual_news
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from tools.features import FEATURES, build_features


BASE = Path(__file__).resolve().parent.parent
DEFAULT_WORKBOOK = BASE / "output" / "event_residual_unbiased_with_news_properties.xlsx"
DEFAULT_JSON = BASE / "output" / "residual_news_experiment.json"
DEFAULT_PREDICTIONS = BASE / "output" / "residual_news_oos_predictions.csv"
DEFAULT_IMPORTANCE = BASE / "output" / "residual_news_feature_importance.csv"
SEARCH_LOG = BASE / "data" / "news_search_log_codex.jsonl"
SEARCH_REMAINING = BASE / "data" / "news_search_remaining.csv"

SAMPLED_ROLES = {"high_residual_case", "matched_low_control"}
TARGET_DERIVED_FEATURES = [
    "divergence",
    "divergence_ma5",
    "nav_discount_pct",
    "nav_discount_delta",
    "nav_discount_vs_ma20",
    "nav_discount_z60",
    "skq_vol20",
]
NUMERIC_FEATURES = [name for name in FEATURES if name not in TARGET_DERIVED_FEATURES]

NEWS_NUMERIC_PROPERTIES = [
    "relevance_score",
    "novelty_score",
    "sentiment_score",
    "surprise_magnitude",
    "materiality_score",
    "uncertainty_score",
    "confidence_score",
    "ex_ante_impact_direction",
]
NEWS_CATEGORICAL_PROPERTIES = [
    "primary_subject",
    "event_type",
    "event_stage",
    "surprise_direction",
    "impact_horizon",
    "event_time_bucket",
    "availability",
]
REGRESSION_MODELS = [
    "mean_baseline",
    "numeric_xgb",
    "news_xgb",
    "combined_xgb",
    "numeric_plus_news_residual_enet",
]
CLASSIFICATION_MODELS = ["numeric_xgb", "news_xgb", "combined_xgb"]


def _date_key(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(8)


def _safe_token(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^0-9a-zA-Z가-힣]+", "_", text).strip("_")
    return text or "unknown"


def _as_list(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        parsed = None
    if isinstance(parsed, (list, tuple, set)):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in re.split(r"[|,;]", text) if part.strip()]


def _search_coverage() -> tuple[dict[str, str], set[str]]:
    statuses: dict[str, list[str]] = defaultdict(list)
    if SEARCH_LOG.exists():
        with SEARCH_LOG.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = str(row.get("trading_date", "")).zfill(8)
                statuses[key].append(str(row.get("status", "")))

    resolved: dict[str, str] = {}
    for key, values in statuses.items():
        completed = [value for value in values if value.startswith("completed_")]
        if completed:
            resolved[key] = completed[-1]
        elif "retrieval_failed" in values:
            resolved[key] = "retrieval_failed"
        elif values:
            resolved[key] = values[-1]

    remaining: set[str] = set()
    if SEARCH_REMAINING.exists():
        pending = pd.read_csv(SEARCH_REMAINING, dtype={"date": str})
        remaining = set(_date_key(pending["date"]))
        # 대기 목록은 수기 갱신이라 낡기 쉽다. 검색 로그에 완료 기록이 있으면
        # 로그를 신뢰한다 (낡은 목록이 표본을 조용히 깎던 문제).
        remaining -= {key for key in remaining
                      if resolved.get(key, "").startswith("completed_")}
    return resolved, remaining


def _aggregate_news(workbook: Path) -> tuple[pd.DataFrame, list[str], dict]:
    raw = pd.read_excel(workbook, sheet_name="event_residual_x_news")
    raw["date_key"] = _date_key(raw["date"])
    daily = (
        raw.sort_values(["date_key", "article_seq"], na_position="last")
        .groupby("date_key", as_index=False)
        .first()
    )[
        [
            "date_key",
            "sampling_role",
            "matched_pair_id",
            "divergence",
            "high_residual",
            "market_regime_20d",
            "market_volatility_bin",
        ]
    ]

    article = raw.loc[raw["article_id"].notna()].copy()
    article["cluster_key"] = article["duplicate_cluster_id"].where(
        article["duplicate_cluster_id"].notna(), article["article_id"]
    )
    article = article.sort_values(
        ["date_key", "relevance_score", "materiality_score", "confidence_score"],
        ascending=[True, False, False, False],
        na_position="last",
    ).drop_duplicates(["date_key", "cluster_key"], keep="first")

    news = pd.DataFrame(index=pd.Index(sorted(raw["date_key"].unique()), name="date_key"))
    article_count = article.groupby("date_key")["article_id"].nunique()
    news["news_article_count"] = article_count
    news["news_has_article"] = news["news_article_count"].fillna(0).gt(0).astype(float)
    news["news_full_text_share"] = (
        article["availability"].eq("full_text").groupby(article["date_key"]).mean()
    )
    news["news_forward_looking_share"] = (
        pd.to_numeric(article["forward_looking"], errors="coerce")
        .groupby(article["date_key"])
        .mean()
    )

    for column in NEWS_NUMERIC_PROPERTIES:
        values = pd.to_numeric(article[column], errors="coerce")
        grouped = values.groupby(article["date_key"])
        news[f"news_{column}_mean"] = grouped.mean()
        news[f"news_{column}_max"] = grouped.max()

    for column in NEWS_CATEGORICAL_PROPERTIES:
        category = article[column].fillna("unknown").map(_safe_token)
        dummies = pd.get_dummies(category, prefix=f"news_{column}", dtype=float)
        dummies["date_key"] = article["date_key"].to_numpy()
        counts = dummies.groupby("date_key").sum()
        news = news.join(counts, how="left")

    mechanisms: list[dict[str, object]] = []
    for row_index, row in article[["date_key", "mechanisms"]].iterrows():
        for mechanism in _as_list(row["mechanisms"]):
            mechanisms.append(
                {
                    "date_key": row["date_key"],
                    "feature": f"news_mechanism_{_safe_token(mechanism)}",
                    "value": 1.0,
                }
            )
    if mechanisms:
        mechanism_frame = pd.DataFrame(mechanisms)
        mechanism_counts = mechanism_frame.pivot_table(
            index="date_key", columns="feature", values="value", aggfunc="sum", fill_value=0
        )
        mechanism_counts.columns.name = None
        news = news.join(mechanism_counts, how="left")

    relevance = pd.to_numeric(article["relevance_score"], errors="coerce").fillna(0)
    materiality = pd.to_numeric(article["materiality_score"], errors="coerce").fillna(0)
    confidence = pd.to_numeric(article["confidence_score"], errors="coerce").fillna(0)
    sentiment = pd.to_numeric(article["sentiment_score"], errors="coerce").fillna(0)
    surprise = pd.to_numeric(article["surprise_magnitude"], errors="coerce").fillna(0)
    direction = pd.to_numeric(article["ex_ante_impact_direction"], errors="coerce").fillna(0)
    article["_sentiment_signal"] = relevance * materiality * confidence * sentiment
    article["_surprise_signal"] = relevance * materiality * confidence * surprise * direction
    article["_attention_signal"] = relevance * materiality * confidence
    for source, target in [
        ("_sentiment_signal", "news_weighted_sentiment_sum"),
        ("_surprise_signal", "news_weighted_surprise_direction_sum"),
        ("_attention_signal", "news_weighted_materiality_sum"),
    ]:
        news[target] = article.groupby("date_key")[source].sum()

    news = news.fillna(0.0).reset_index()
    news_features = [column for column in news.columns if column != "date_key"]
    daily = daily.merge(news, on="date_key", how="left")
    daily[news_features] = daily[news_features].fillna(0.0)

    roles = (
        daily.groupby("sampling_role")["date_key"].nunique().sort_index().to_dict()
    )
    news_days_by_role = (
        daily.assign(has_news=daily["news_has_article"].gt(0))
        .groupby("sampling_role")["has_news"]
        .sum()
        .astype(int)
        .to_dict()
    )
    audit = {
        "workbook_rows": int(len(raw)),
        "workbook_unique_dates": int(raw["date_key"].nunique()),
        "article_rows_before_cluster_dedup": int(raw["article_id"].notna().sum()),
        "article_rows_after_cluster_dedup": int(len(article)),
        "sample_roles_unique_dates": {str(k): int(v) for k, v in roles.items()},
        "news_dates_by_role": {str(k): int(v) for k, v in news_days_by_role.items()},
    }
    return daily, news_features, audit


def _build_dataset(workbook: Path) -> tuple[pd.DataFrame, list[str], dict]:
    daily, news_features, audit = _aggregate_news(workbook)
    numeric = build_features().copy()
    numeric["date_key"] = numeric["date"].dt.strftime("%Y%m%d")
    keep = ["date_key", "divergence", *NUMERIC_FEATURES]
    numeric = numeric[keep].rename(columns={"divergence": "numeric_divergence"})
    frame = daily.merge(numeric, on="date_key", how="inner")

    status, remaining = _search_coverage()
    frame["search_status"] = frame["date_key"].map(status)
    frame["search_complete"] = frame["search_status"].fillna("").str.startswith("completed_")
    frame.loc[frame["date_key"].isin(remaining), "search_complete"] = False
    before_coverage = int(frame["sampling_role"].isin(SAMPLED_ROLES).sum())
    frame = frame.loc[
        frame["sampling_role"].isin(SAMPLED_ROLES)
        & frame["search_complete"]
        & frame["numeric_divergence"].notna()
    ].copy()
    frame = frame.sort_values("date_key").drop_duplicates("date_key").reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date_key"], format="%Y%m%d")
    frame["target"] = pd.to_numeric(frame["numeric_divergence"], errors="coerce")
    frame["high_case"] = frame["sampling_role"].eq("high_residual_case").astype(int)

    constant_news = [column for column in news_features if frame[column].nunique(dropna=False) <= 1]
    news_features = [column for column in news_features if column not in constant_news]
    audit.update(
        {
            "claimed_feature_count": 27,
            "repository_feature_count": int(len(FEATURES)),
            "target_derived_features_excluded": TARGET_DERIVED_FEATURES,
            "numeric_feature_count_used": int(len(NUMERIC_FEATURES)),
            "news_feature_count_used": int(len(news_features)),
            "sampled_dates_before_search_coverage_filter": before_coverage,
            "model_dates_after_search_coverage_filter": int(len(frame)),
            "remaining_search_dates": int(len(remaining)),
            "model_date_start": frame["date_key"].min(),
            "model_date_end": frame["date_key"].max(),
            "constant_news_features_removed": constant_news,
        }
    )
    return frame, news_features, audit


def _folds(n: int) -> list[tuple[int, int]]:
    edges = np.linspace(int(n * 0.4), n, 5).astype(int)
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def _xgb_regressor(seed: int) -> XGBRegressor:
    return XGBRegressor(
        n_estimators=220,
        learning_rate=0.03,
        max_depth=2,
        min_child_weight=10,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_alpha=0.15,
        reg_lambda=5.0,
        objective="reg:squarederror",
        n_jobs=1,
        random_state=seed,
    )


def _xgb_classifier(seed: int, positive: int, total: int) -> XGBClassifier:
    negative = total - positive
    weight = float(negative / positive) if positive else 1.0
    return XGBClassifier(
        n_estimators=180,
        learning_rate=0.03,
        max_depth=2,
        min_child_weight=10,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_alpha=0.15,
        reg_lambda=5.0,
        eval_metric="logloss",
        scale_pos_weight=weight,
        n_jobs=1,
        random_state=seed,
    )


def _fit_regressor(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    seed: int,
) -> tuple[np.ndarray, XGBRegressor, SimpleImputer, np.ndarray, np.ndarray]:
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train[features])
    x_test = imputer.transform(test[features])
    model = _xgb_regressor(seed)
    model.fit(x_train, train["target"])
    return model.predict(x_test), model, imputer, x_train, x_test


def _cross_fitted_numeric_residuals(train: pd.DataFrame, seed: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(train)
    predictions = np.full(n, np.nan)
    start = max(80, int(n * 0.4))
    edges = np.linspace(start, n, 4).astype(int)
    for inner_no, (a, b) in enumerate(zip(edges[:-1], edges[1:]), 1):
        if b <= a:
            continue
        imputer = SimpleImputer(strategy="median")
        x_train = imputer.fit_transform(train.iloc[:a][NUMERIC_FEATURES])
        x_test = imputer.transform(train.iloc[a:b][NUMERIC_FEATURES])
        model = _xgb_regressor(seed + inner_no)
        model.fit(x_train, train.iloc[:a]["target"])
        predictions[a:b] = model.predict(x_test)
    mask = np.isfinite(predictions)
    residuals = train["target"].to_numpy()[mask] - predictions[mask]
    return mask, residuals


def _metric_regression(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "r2": round(float(r2_score(actual, predicted)), 4),
        "mae_pctp": round(float(mean_absolute_error(actual, predicted)), 4),
        "sign_accuracy": round(float((np.sign(actual) == np.sign(predicted)).mean()), 4),
    }


def _metric_classification(actual: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "pr_auc": round(float(average_precision_score(actual, probability)), 4),
        "roc_auc": round(float(roc_auc_score(actual, probability)), 4),
        "brier": round(float(brier_score_loss(actual, probability)), 4),
    }


def _block_bootstrap_mae_delta(
    actual: np.ndarray,
    baseline: np.ndarray,
    challenger: np.ndarray,
    seed: int = 20260806,
    block: int = 20,
    repeats: int = 2000,
) -> dict[str, float]:
    improvement = np.abs(actual - baseline) - np.abs(actual - challenger)
    n = len(improvement)
    rng = np.random.default_rng(seed)
    means = np.empty(repeats)
    max_start = max(1, n - block + 1)
    for index in range(repeats):
        sample: list[int] = []
        while len(sample) < n:
            start = int(rng.integers(0, max_start))
            sample.extend(range(start, min(start + block, n)))
        means[index] = improvement[np.asarray(sample[:n])].mean()
    return {
        "mean_mae_improvement_pctp": round(float(improvement.mean()), 4),
        "ci95_low": round(float(np.quantile(means, 0.025)), 4),
        "ci95_high": round(float(np.quantile(means, 0.975)), 4),
        "block_length": int(block),
        "bootstrap_repeats": int(repeats),
    }


def run(
    workbook: Path = DEFAULT_WORKBOOK,
    output_json: Path = DEFAULT_JSON,
    output_predictions: Path = DEFAULT_PREDICTIONS,
    output_importance: Path = DEFAULT_IMPORTANCE,
) -> dict:
    frame, news_features, audit = _build_dataset(workbook)
    combined_features = [*NUMERIC_FEATURES, *news_features]
    n = len(frame)
    regression_predictions = {
        name: np.full(n, np.nan, dtype=float) for name in REGRESSION_MODELS
    }
    class_probabilities = {
        name: np.full(n, np.nan, dtype=float) for name in CLASSIFICATION_MODELS
    }
    fold_label = np.full(n, np.nan)
    fold_results: list[dict] = []
    gain_importance: dict[str, list[float]] = defaultdict(list)
    permutation_importance: dict[str, list[float]] = defaultdict(list)
    enet_coefficients: dict[str, list[float]] = defaultdict(list)

    for fold_no, (a, b) in enumerate(_folds(n), 1):
        train = frame.iloc[:a]
        test = frame.iloc[a:b]
        fold_label[a:b] = fold_no
        y_test = test["target"].to_numpy()
        regression_predictions["mean_baseline"][a:b] = float(train["target"].mean())

        numeric_pred, numeric_model, numeric_imputer, _, _ = _fit_regressor(
            train, test, NUMERIC_FEATURES, seed=100 + fold_no
        )
        news_pred, _, _, _, _ = _fit_regressor(
            train, test, news_features, seed=200 + fold_no
        )
        combined_pred, combined_model, combined_imputer, _, combined_test = _fit_regressor(
            train, test, combined_features, seed=300 + fold_no
        )
        regression_predictions["numeric_xgb"][a:b] = numeric_pred
        regression_predictions["news_xgb"][a:b] = news_pred
        regression_predictions["combined_xgb"][a:b] = combined_pred

        residual_mask, residual_target = _cross_fitted_numeric_residuals(
            train, seed=400 + fold_no * 10
        )
        residual_model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            ElasticNet(alpha=0.04, l1_ratio=0.25, max_iter=10000, random_state=fold_no),
        )
        residual_model.fit(train.loc[residual_mask, news_features], residual_target)
        residual_correction = residual_model.predict(test[news_features])
        regression_predictions["numeric_plus_news_residual_enet"][a:b] = (
            numeric_pred + residual_correction
        )
        coefficients = residual_model.named_steps["elasticnet"].coef_
        for feature, coefficient in zip(news_features, coefficients):
            enet_coefficients[feature].append(float(coefficient))

        for feature, importance in zip(combined_features, combined_model.feature_importances_):
            if feature in news_features:
                gain_importance[feature].append(float(importance))

        base_mae = mean_absolute_error(y_test, combined_pred)
        rng = np.random.default_rng(500 + fold_no)
        news_offset = len(NUMERIC_FEATURES)
        for feature_no, feature in enumerate(news_features):
            deltas = []
            column_no = news_offset + feature_no
            for _ in range(3):
                shuffled = combined_test.copy()
                shuffled[:, column_no] = rng.permutation(shuffled[:, column_no])
                shuffled_pred = combined_model.predict(shuffled)
                deltas.append(mean_absolute_error(y_test, shuffled_pred) - base_mae)
            permutation_importance[feature].append(float(np.mean(deltas)))

        for model_name, features in [
            ("numeric_xgb", NUMERIC_FEATURES),
            ("news_xgb", news_features),
            ("combined_xgb", combined_features),
        ]:
            imputer = SimpleImputer(strategy="median")
            x_train = imputer.fit_transform(train[features])
            x_test = imputer.transform(test[features])
            classifier = _xgb_classifier(
                seed=600 + fold_no,
                positive=int(train["high_case"].sum()),
                total=len(train),
            )
            classifier.fit(x_train, train["high_case"])
            class_probabilities[model_name][a:b] = classifier.predict_proba(x_test)[:, 1]

        fold_metrics = {
            name: _metric_regression(y_test, values[a:b])
            for name, values in regression_predictions.items()
        }
        fold_results.append(
            {
                "fold": fold_no,
                "period": [test["date_key"].iloc[0], test["date_key"].iloc[-1]],
                "n": int(len(test)),
                "regression": fold_metrics,
                "delta_combined_vs_numeric": {
                    "delta_r2": round(
                        fold_metrics["combined_xgb"]["r2"]
                        - fold_metrics["numeric_xgb"]["r2"],
                        4,
                    ),
                    "mae_improvement_pctp": round(
                        fold_metrics["numeric_xgb"]["mae_pctp"]
                        - fold_metrics["combined_xgb"]["mae_pctp"],
                        4,
                    ),
                },
            }
        )

    oos_mask = np.isfinite(regression_predictions["mean_baseline"])
    actual = frame.loc[oos_mask, "target"].to_numpy()
    actual_class = frame.loc[oos_mask, "high_case"].to_numpy()
    regression_aggregate = {
        name: _metric_regression(actual, values[oos_mask])
        for name, values in regression_predictions.items()
    }
    classification_aggregate = {
        name: _metric_classification(actual_class, values[oos_mask])
        for name, values in class_probabilities.items()
    }
    numeric_metrics = regression_aggregate["numeric_xgb"]
    combined_metrics = regression_aggregate["combined_xgb"]
    residual_metrics = regression_aggregate["numeric_plus_news_residual_enet"]

    importance_rows = []
    for feature in news_features:
        perm = permutation_importance.get(feature, [])
        gain = gain_importance.get(feature, [])
        coefficients = enet_coefficients.get(feature, [])
        nonzero = [value for value in coefficients if abs(value) > 1e-10]
        if nonzero:
            positive = sum(value > 0 for value in nonzero)
            negative = sum(value < 0 for value in nonzero)
            sign_consistency = max(positive, negative) / len(nonzero)
        else:
            sign_consistency = 0.0
        importance_rows.append(
            {
                "feature": feature,
                "oos_permutation_mae_increase_mean": float(np.mean(perm)) if perm else 0.0,
                "oos_permutation_positive_folds": int(sum(value > 0 for value in perm)),
                "xgb_gain_mean": float(np.mean(gain)) if gain else 0.0,
                "residual_enet_coefficient_mean": float(np.mean(coefficients)) if coefficients else 0.0,
                "residual_enet_nonzero_folds": int(len(nonzero)),
                "residual_enet_sign_consistency": float(sign_consistency),
            }
        )
    importance_frame = pd.DataFrame(importance_rows).sort_values(
        ["oos_permutation_positive_folds", "oos_permutation_mae_increase_mean", "xgb_gain_mean"],
        ascending=[False, False, False],
    )

    output_predictions.parent.mkdir(parents=True, exist_ok=True)
    prediction_frame = frame[
        ["date_key", "sampling_role", "matched_pair_id", "target", "high_case"]
    ].copy()
    prediction_frame["fold"] = fold_label
    for name, values in regression_predictions.items():
        prediction_frame[f"pred_{name}"] = values
    for name, values in class_probabilities.items():
        prediction_frame[f"prob_high_{name}"] = values
    prediction_frame.to_csv(output_predictions, index=False, encoding="utf-8-sig")
    importance_frame.to_csv(output_importance, index=False, encoding="utf-8-sig")

    result = {
        "experiment": "residual_news_matched_oos_v1",
        "target": "divergence = skq_ret - nav_implied_ret",
        "scope": "matched high-residual cases and low controls with completed identical news searches",
        "validation": "expanding window; first 40% seed; four OOS blocks; date-level aggregation",
        "data_audit": audit,
        "regression": {
            "n_oos": int(oos_mask.sum()),
            "aggregate": regression_aggregate,
            "incremental_news": {
                "combined_xgb_vs_numeric_xgb": {
                    "delta_r2": round(combined_metrics["r2"] - numeric_metrics["r2"], 4),
                    "mae_improvement_pctp": round(
                        numeric_metrics["mae_pctp"] - combined_metrics["mae_pctp"], 4
                    ),
                    "mae_block_bootstrap": _block_bootstrap_mae_delta(
                        actual,
                        regression_predictions["numeric_xgb"][oos_mask],
                        regression_predictions["combined_xgb"][oos_mask],
                    ),
                },
                "residual_enet_vs_numeric_xgb": {
                    "delta_r2": round(residual_metrics["r2"] - numeric_metrics["r2"], 4),
                    "mae_improvement_pctp": round(
                        numeric_metrics["mae_pctp"] - residual_metrics["mae_pctp"], 4
                    ),
                    "mae_block_bootstrap": _block_bootstrap_mae_delta(
                        actual,
                        regression_predictions["numeric_xgb"][oos_mask],
                        regression_predictions["numeric_plus_news_residual_enet"][oos_mask],
                        seed=20260807,
                    ),
                },
            },
            "folds": fold_results,
        },
        "classification": {
            "target": "sampling role: high_residual_case vs matched_low_control",
            "positive_rate_oos": round(float(actual_class.mean()), 4),
            "aggregate": classification_aggregate,
            "calibration_warning": "Matched case/control probabilities are not population probabilities.",
        },
        "top_news_drivers": importance_frame.head(20).round(6).to_dict(orient="records"),
        "interpretation_guardrails": [
            "News results are associations within the matched searched sample, not full-population effects.",
            "Positive permutation importance must repeat across time folds before a feature is treated as stable.",
            "Elastic Net coefficient direction is descriptive and is not a causal effect.",
            "The repository currently defines 37 total features and 30 leakage-screened numeric inputs, not 27.",
            "Same-day market-flow fields are suitable for post-close attribution, not pre-close forecasting.",
        ],
        "artifacts": {
            "predictions": str(output_predictions.relative_to(BASE)),
            "feature_importance": str(output_importance.relative_to(BASE)),
        },
    }
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output-importance", type=Path, default=DEFAULT_IMPORTANCE)
    args = parser.parse_args()
    result = run(
        workbook=args.workbook,
        output_json=args.output_json,
        output_predictions=args.output_predictions,
        output_importance=args.output_importance,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
