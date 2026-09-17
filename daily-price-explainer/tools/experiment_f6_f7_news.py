"""
F6(기업 뉴스) · F7(매크로 뉴스) 분할 검증.

Phase 1 은 뉴스 84개 피처를 통째로 넣어 기각됐다. 이번에는 기사를
primary_subject 로 버킷 분할해 재집계하고, 각 버킷이 divergence 를 설명하는지
따로 검정한다. 가설은 "기업 뉴스 신호가 매크로 뉴스 잡음에 희석되어 안 보였을
수 있다"이다.

버킷 (기사 958건 기준):
  F6 기업   sk_square 380 + portfolio_company 299 + governance 63
            + shareholder_policy 53  = 795건
  F7 매크로 macro_market 66 + semiconductor_sector 41 + regulation 2 = 109건
  HX 하이닉스 sk_hynix 30  — 하이닉스 주가는 1단계에서 이미 통제되므로 이중계산
            우려가 있어 별도 관찰만 한다
  제외      other 16, market_flow 8 (market_flow 는 Phase 1 에서 저품질 확인)

검증 설계는 experiment_residual_news.py 와 동일하다 — 같은 매칭 case/control
표본, 같은 확장윈도우(40% seed + 4블록), 같은 누설 차단. 따라서 numeric 기준선이
그쪽 numeric_xgb 와 일치해야 한다.

모델: 평균 기준선 / OLS / Ridge / MLP 2층 / XGBoost  (+ XGBoost SHAP)
지표: 회귀 R²·MAE·부호정확도, 분류 ROC-AUC·PR-AUC·Brier, 블록 부트스트랩 CI

Usage:
  python tools/experiment_f6_f7_news.py
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
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from tools.features import build_features
from tools.experiment_residual_news import (
    DEFAULT_WORKBOOK,
    NEWS_CATEGORICAL_PROPERTIES,
    NEWS_NUMERIC_PROPERTIES,
    NUMERIC_FEATURES,
    SAMPLED_ROLES,
    TARGET_DERIVED_FEATURES,
    _as_list,
    _date_key,
    _safe_token,
    _search_coverage,
    _xgb_classifier,
    _xgb_regressor,
)

DEFAULT_OUTPUT = BASE / "output" / "f6_f7_news_experiment.json"

BUCKETS: dict[str, set[str]] = {
    "f6": {"sk_square", "portfolio_company", "governance", "shareholder_policy"},
    "f7": {"macro_market", "semiconductor_sector", "regulation"},
    "hx": {"sk_hynix"},
}
EXCLUDED_SUBJECTS = {"other", "market_flow"}

SEEDS = (0, 1, 2)
BLOCK_LENGTH = 20
BOOTSTRAP_REPEATS = 2000


# ── 집계 ────────────────────────────────────────────────────────────────────
def _aggregate_bucket(article: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """experiment_residual_news._aggregate_news 와 동일 스킴, 버킷 한정."""
    idx = pd.Index(sorted(article["date_key"].unique()), name="date_key")
    out = pd.DataFrame(index=idx)

    out[f"{prefix}_article_count"] = article.groupby("date_key")["article_id"].nunique()
    out[f"{prefix}_has_article"] = out[f"{prefix}_article_count"].fillna(0).gt(0).astype(float)
    out[f"{prefix}_full_text_share"] = (
        article["availability"].eq("full_text").groupby(article["date_key"]).mean()
    )
    out[f"{prefix}_forward_looking_share"] = (
        pd.to_numeric(article["forward_looking"], errors="coerce")
        .groupby(article["date_key"]).mean()
    )

    for column in NEWS_NUMERIC_PROPERTIES:
        values = pd.to_numeric(article[column], errors="coerce")
        grouped = values.groupby(article["date_key"])
        out[f"{prefix}_{column}_mean"] = grouped.mean()
        out[f"{prefix}_{column}_max"] = grouped.max()

    # primary_subject 는 버킷 정의 자체라 원핫에서 제외 (버킷 내 상수)
    for column in [c for c in NEWS_CATEGORICAL_PROPERTIES if c != "primary_subject"]:
        category = article[column].fillna("unknown").map(_safe_token)
        dummies = pd.get_dummies(category, prefix=f"{prefix}_{column}", dtype=float)
        dummies["date_key"] = article["date_key"].to_numpy()
        out = out.join(dummies.groupby("date_key").sum(), how="left")

    mechanisms: list[dict[str, object]] = []
    for _, row in article[["date_key", "mechanisms"]].iterrows():
        for mechanism in _as_list(row["mechanisms"]):
            mechanisms.append({
                "date_key": row["date_key"],
                "feature": f"{prefix}_mechanism_{_safe_token(mechanism)}",
                "value": 1.0,
            })
    if mechanisms:
        counts = pd.DataFrame(mechanisms).pivot_table(
            index="date_key", columns="feature", values="value",
            aggfunc="sum", fill_value=0,
        )
        counts.columns.name = None
        out = out.join(counts, how="left")

    rel = pd.to_numeric(article["relevance_score"], errors="coerce").fillna(0)
    mat = pd.to_numeric(article["materiality_score"], errors="coerce").fillna(0)
    con = pd.to_numeric(article["confidence_score"], errors="coerce").fillna(0)
    sen = pd.to_numeric(article["sentiment_score"], errors="coerce").fillna(0)
    sur = pd.to_numeric(article["surprise_magnitude"], errors="coerce").fillna(0)
    dirc = pd.to_numeric(article["ex_ante_impact_direction"], errors="coerce").fillna(0)
    tmp = article.assign(
        _sent=rel * mat * con * sen,
        _surp=rel * mat * con * sur * dirc,
        _attn=rel * mat * con,
    )
    for src, tgt in [("_sent", f"{prefix}_weighted_sentiment_sum"),
                     ("_surp", f"{prefix}_weighted_surprise_direction_sum"),
                     ("_attn", f"{prefix}_weighted_materiality_sum")]:
        out[tgt] = tmp.groupby("date_key")[src].sum()

    return out.fillna(0.0)


def _build_dataset(workbook: Path) -> tuple[pd.DataFrame, dict[str, list[str]], dict]:
    raw = pd.read_excel(workbook, sheet_name="event_residual_x_news")
    raw["date_key"] = _date_key(raw["date"])

    daily = (
        raw.sort_values(["date_key", "article_seq"], na_position="last")
        .groupby("date_key", as_index=False).first()
    )[["date_key", "sampling_role", "matched_pair_id", "divergence", "high_residual"]]

    article = raw.loc[raw["article_id"].notna()].copy()
    article["cluster_key"] = article["duplicate_cluster_id"].where(
        article["duplicate_cluster_id"].notna(), article["article_id"]
    )
    article = article.sort_values(
        ["date_key", "relevance_score", "materiality_score", "confidence_score"],
        ascending=[True, False, False, False], na_position="last",
    ).drop_duplicates(["date_key", "cluster_key"], keep="first")
    article["subject"] = article["primary_subject"].fillna("unknown").map(_safe_token)

    subject_counts = article["subject"].value_counts().to_dict()

    bucket_features: dict[str, list[str]] = {}
    for name, subjects in BUCKETS.items():
        sub = article.loc[article["subject"].isin(subjects)]
        if sub.empty:
            bucket_features[name] = []
            continue
        agg = _aggregate_bucket(sub, name).reset_index()
        feats = [c for c in agg.columns if c != "date_key"]
        bucket_features[name] = feats
        daily = daily.merge(agg, on="date_key", how="left")
        daily[feats] = daily[feats].fillna(0.0)

    numeric = build_features().copy()
    numeric["date_key"] = numeric["date"].dt.strftime("%Y%m%d")
    numeric = numeric[["date_key", "divergence", *NUMERIC_FEATURES]].rename(
        columns={"divergence": "numeric_divergence"}
    )
    frame = daily.merge(numeric, on="date_key", how="inner")

    status, remaining = _search_coverage()
    frame["search_status"] = frame["date_key"].map(status)
    frame["search_complete"] = frame["search_status"].fillna("").str.startswith("completed_")
    frame.loc[frame["date_key"].isin(remaining), "search_complete"] = False
    frame = frame.loc[
        frame["sampling_role"].isin(SAMPLED_ROLES)
        & frame["search_complete"]
        & frame["numeric_divergence"].notna()
    ].copy()
    frame = frame.sort_values("date_key").drop_duplicates("date_key").reset_index(drop=True)
    frame["target"] = pd.to_numeric(frame["numeric_divergence"], errors="coerce")
    frame["high_case"] = frame["sampling_role"].eq("high_residual_case").astype(int)

    # 버킷 내 상수 피처 제거
    for name, feats in bucket_features.items():
        bucket_features[name] = [f for f in feats if frame[f].nunique(dropna=False) > 1]

    audit = {
        "workbook_rows": int(len(raw)),
        "article_rows_after_cluster_dedup": int(len(article)),
        "subject_counts": subject_counts,
        "bucket_definition": {k: sorted(v) for k, v in BUCKETS.items()},
        "excluded_subjects": sorted(EXCLUDED_SUBJECTS),
        "bucket_article_counts": {
            k: int(article["subject"].isin(v).sum()) for k, v in BUCKETS.items()
        },
        "bucket_days_with_article": {
            k: int((frame[f"{k}_has_article"] > 0).sum()) if f"{k}_has_article" in frame else 0
            for k in BUCKETS
        },
        "bucket_feature_counts": {k: len(v) for k, v in bucket_features.items()},
        "numeric_feature_count": len(NUMERIC_FEATURES),
        "excluded_target_derived": TARGET_DERIVED_FEATURES,
        "model_dates": int(len(frame)),
        "date_start": frame["date_key"].min(),
        "date_end": frame["date_key"].max(),
    }
    return frame, bucket_features, audit


# ── 모델 ────────────────────────────────────────────────────────────────────
def _folds(n: int) -> list[tuple[int, int]]:
    edges = np.linspace(int(n * 0.4), n, 5).astype(int)
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def _xgb_reg(seed: int) -> XGBRegressor:
    """매칭 표본용 설정. experiment_residual_news 와 동일해야 numeric_30 기준선이
    그쪽 numeric_xgb(0.2811)와 일치한다. 전체기간 실험(experiment_unbiased_dl)의
    설정(max_depth=3, reg_lambda=2)을 쓰면 표본이 작아 과적합한다."""
    return _xgb_regressor(seed)


def _run_models(frame: pd.DataFrame, feats: list[str]) -> dict[str, np.ndarray]:
    """확장윈도우 OOS 예측. feats 가 비면 평균 기준선만."""
    y = frame["target"].astype(float)
    n = len(y)
    names = ["mean_baseline"] if not feats else [
        "mean_baseline", "ols", "ridge", "mlp_2layer", "xgboost"
    ]
    pred = {k: np.full(n, np.nan) for k in names}

    for a, b in _folds(n):
        y_tr = y.iloc[:a]
        pred["mean_baseline"][a:b] = float(y_tr.mean())
        if not feats:
            continue
        imp = SimpleImputer(strategy="median")
        x_tr = imp.fit_transform(frame[feats].iloc[:a])
        x_te = imp.transform(frame[feats].iloc[a:b])

        pred["ols"][a:b] = make_pipeline(
            StandardScaler(), LinearRegression()
        ).fit(x_tr, y_tr).predict(x_te)

        pred["ridge"][a:b] = make_pipeline(
            StandardScaler(), Ridge(alpha=10.0)
        ).fit(x_tr, y_tr).predict(x_te)

        mlps = []
        for s in SEEDS:
            m = make_pipeline(StandardScaler(), MLPRegressor(
                hidden_layer_sizes=(32, 16), alpha=0.01, early_stopping=True,
                max_iter=800, random_state=s,
            ))
            m.fit(x_tr, y_tr)
            mlps.append(m.predict(x_te))
        pred["mlp_2layer"][a:b] = np.mean(mlps, axis=0)

        xgbs = [_xgb_reg(s).fit(x_tr, y_tr).predict(x_te) for s in SEEDS]
        pred["xgboost"][a:b] = np.mean(xgbs, axis=0)

    return pred


def _classify(frame: pd.DataFrame, feats: list[str]) -> np.ndarray:
    """case vs control 판별 확률 (XGBoost)."""
    y = frame["high_case"].astype(int)
    n = len(y)
    prob = np.full(n, np.nan)
    for a, b in _folds(n):
        y_tr = y.iloc[:a]
        imp = SimpleImputer(strategy="median")
        x_tr = imp.fit_transform(frame[feats].iloc[:a])
        x_te = imp.transform(frame[feats].iloc[a:b])
        clf = _xgb_classifier(0, int(y_tr.sum()), len(y_tr))
        clf.fit(x_tr, y_tr)
        prob[a:b] = clf.predict_proba(x_te)[:, 1]
    return prob


def _reg_metrics(actual: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "r2": round(float(r2_score(actual, pred)), 4),
        "mae_pctp": round(float(mean_absolute_error(actual, pred)), 4),
        "sign_accuracy": round(float((np.sign(actual) == np.sign(pred)).mean()), 4),
    }


def _clf_metrics(actual: np.ndarray, prob: np.ndarray) -> dict:
    return {
        "roc_auc": round(float(roc_auc_score(actual, prob)), 4),
        "pr_auc": round(float(average_precision_score(actual, prob)), 4),
        "brier": round(float(brier_score_loss(actual, prob)), 4),
    }


def _block_ci(diff: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(0)
    n = len(diff)
    nb = int(np.ceil(n / BLOCK_LENGTH))
    max_start = max(n - BLOCK_LENGTH, 0)
    starts = rng.integers(0, max_start + 1, size=(BOOTSTRAP_REPEATS, nb))
    idx = (starts[:, :, None] + np.arange(BLOCK_LENGTH)[None, None, :])
    idx = np.minimum(idx.reshape(BOOTSTRAP_REPEATS, -1)[:, :n], n - 1)
    means = diff[idx].mean(axis=1)
    return (round(float(np.percentile(means, 2.5)), 4),
            round(float(np.percentile(means, 97.5)), 4))


def _compare(actual: np.ndarray, ref: np.ndarray, test: np.ndarray) -> dict:
    diff = np.abs(actual - ref) - np.abs(actual - test)
    lo, hi = _block_ci(diff)
    return {
        "mae_improvement_pctp": round(float(diff.mean()), 4),
        "ci95_low": lo, "ci95_high": hi, "significant": bool(lo > 0),
    }


def _shap_top(frame: pd.DataFrame, feats: list[str], top: int = 20) -> list | str:
    try:
        import shap
    except ImportError:
        return "shap 미설치 — 건너뜀"
    n = len(frame)
    a = int(n * 0.4)
    imp = SimpleImputer(strategy="median")
    x_tr = imp.fit_transform(frame[feats].iloc[:a])
    model = _xgb_reg(0).fit(x_tr, frame["target"].astype(float).iloc[:a])
    x_all = imp.transform(frame[feats])
    sv = shap.TreeExplainer(model).shap_values(x_all)
    imp_vals = np.abs(sv).mean(axis=0)
    order = np.argsort(-imp_vals)[:top]
    total = float(imp_vals.sum()) or 1.0
    return [
        {"feature": feats[i], "mean_abs_shap": round(float(imp_vals[i]), 6),
         "share": round(float(imp_vals[i]) / total, 4)}
        for i in order
    ]


# ── 실행 ────────────────────────────────────────────────────────────────────
def run(workbook: Path = DEFAULT_WORKBOOK, output: Path = DEFAULT_OUTPUT) -> dict:
    warnings.filterwarnings("ignore")
    frame, buckets, audit = _build_dataset(workbook)

    f6, f7, hx = buckets.get("f6", []), buckets.get("f7", []), buckets.get("hx", [])
    sets: dict[str, list[str]] = {
        "baseline": [],
        "numeric_30": list(NUMERIC_FEATURES),
        "numeric_plus_f6": list(NUMERIC_FEATURES) + f6,
        "numeric_plus_f7": list(NUMERIC_FEATURES) + f7,
        "numeric_plus_f6_f7": list(NUMERIC_FEATURES) + f6 + f7,
        "numeric_plus_all_news": list(NUMERIC_FEATURES) + f6 + f7 + hx,
        "f6_only": f6,
        "f7_only": f7,
    }

    y = frame["target"].astype(float)
    preds = {name: _run_models(frame, feats) for name, feats in sets.items()}
    mask = ~np.isnan(preds["baseline"]["mean_baseline"])
    actual = y.to_numpy()[mask]
    base_pred = preds["baseline"]["mean_baseline"][mask]
    num_xgb = preds["numeric_30"]["xgboost"][mask]

    regression: dict[str, dict] = {}
    for name, pset in preds.items():
        entry = {"n_features": len(sets[name]), "models": {}}
        for model, values in pset.items():
            entry["models"][model] = _reg_metrics(actual, values[mask])
        if name != "baseline":
            entry["xgb_vs_baseline"] = _compare(actual, base_pred, pset["xgboost"][mask])
            if name != "numeric_30":
                entry["xgb_vs_numeric30"] = _compare(actual, num_xgb, pset["xgboost"][mask])
        regression[name] = entry

    clf_sets = {k: v for k, v in sets.items() if v}
    classification = {}
    actual_cls = frame["high_case"].to_numpy()[mask]
    for name, feats in clf_sets.items():
        prob = _classify(frame, feats)
        classification[name] = _clf_metrics(actual_cls, prob[mask])
    classification["_positive_rate_oos"] = round(float(actual_cls.mean()), 4)

    result = {
        "experiment": "f6_f7_news_split_v1",
        "as_of": frame["date_key"].max(),
        "target": "divergence (matched case/control sample)",
        "validation": (
            "expanding window; first 40% seed; four OOS blocks; "
            f"moving-block bootstrap ({BLOCK_LENGTH}d, {BOOTSTRAP_REPEATS} reps); "
            "XGB/MLP averaged over 3 seeds"
        ),
        "hypothesis": (
            "Phase 1 은 뉴스 84개를 통째로 넣어 기각됐다. 기업 뉴스 신호가 매크로 뉴스에 "
            "희석됐을 가능성을 버킷 분할로 검정한다."
        ),
        "audit": audit,
        "n_oos": int(mask.sum()),
        "regression": regression,
        "classification": classification,
        "shap_top_numeric_plus_f6_f7": _shap_top(frame, sets["numeric_plus_f6_f7"]),
        "guardrails": [
            "F7(매크로) 기사는 109건뿐이라 표본력이 낮다. 미달이 곧 신호 부재를 뜻하지 않는다.",
            "hx(sk_hynix) 버킷은 하이닉스 주가가 1단계에서 이미 통제되므로 이중계산 우려가 있어 참고용이다.",
            "매칭 표본 확률은 모집단 확률이 아니다.",
            "significant=true 는 MAE 개선 CI 하한이 0을 넘는다는 뜻이며 실무적 크기는 별도 판단이 필요하다.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return result


def _summary(r: dict) -> None:
    a = r["audit"]
    print("=" * 94)
    print(f"  F6/F7 뉴스 분할 검증 — OOS {r['n_oos']}일 / 모델 대상 {a['model_dates']}일")
    print("=" * 94)
    print(f"  버킷 기사수: {a['bucket_article_counts']}")
    print(f"  기사 있는 날: {a['bucket_days_with_article']}")
    print(f"  버킷 피처수: {a['bucket_feature_counts']}")
    print()
    print(f"  {'피처셋':24} {'n':>4} {'모델':<12} {'R²':>9} {'MAE':>8} {'부호':>6}")
    for name, e in r["regression"].items():
        for model, m in e["models"].items():
            if model == "mean_baseline" and name != "baseline":
                continue
            print(f"  {name:24} {e['n_features']:>4} {model:<12} "
                  f"{m['r2']:>+9.4f} {m['mae_pctp']:>8.4f} {m['sign_accuracy']:>6.3f}")
    print()
    print(f"  ── 증분 검정 (XGBoost, 숫자30 대비) ──")
    for name, e in r["regression"].items():
        v = e.get("xgb_vs_numeric30")
        if v:
            ci = f"[{v['ci95_low']:+.3f}, {v['ci95_high']:+.3f}]"
            print(f"  {name:24} ΔMAE {v['mae_improvement_pctp']:>+8.4f} {ci:>18}  "
                  f"{'유의' if v['significant'] else '미달'}")
    print()
    print(f"  ── 분류 (case vs control), 양성률 {r['classification']['_positive_rate_oos']} ──")
    for name, m in r["classification"].items():
        if name.startswith("_"):
            continue
        print(f"  {name:24} ROC-AUC {m['roc_auc']:.4f}  PR-AUC {m['pr_auc']:.4f}  "
              f"Brier {m['brier']:.4f}")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()
    res = run(args.workbook, args.output)
    _summary(res)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
