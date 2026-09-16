"""
XGBoost Factor Attribution + SHAP 일별 기여분 분해
data/factor_log.csv → tools.features.get_xy() (F1~F7 taxonomy 피처) 를 입력으로,
비선형 팩터 귀인(XGBoost)과 SHAP 기반 "오늘 주가가 왜 움직였나" 분해를 제공한다.

requirements.txt 의 계획 구현:
  xgboost>=2.0.0  — 비선형 팩터 귀인 (250행+ 누적 후)
  shap>=0.45.0    — XGBoost 일별 기여분 분해(SHAP)

사용:
  python main.py attribution            # 전 구간 글로벌 중요도(gain + SHAP) + 카테고리 귀인
  python main.py attribution 20260616   # 해당 일자 SHAP 기여분 분해(왜 그날 움직였나)

  from tools.attribution import train, global_importance, explain_day
  model, X, y, dates = train()
  explain_day("20260616")

────────────────────────────────────────────────────────────────────────
분석 요약 (2026-06, 직접구현 XGBoost 백테스트 기준 — 본 모듈은 실 xgboost로 재현):
  · 당일 설명(attribution): 시계열 OOS R²≈0.57. 최상위 동인은 nav_implied_ret(=하이닉스
    기반 NAV 펀더멘털)·KOSPI·하이닉스. 단순 3팩터(하이닉스·반도체·KOSPI) 선형모델이
    이미 R²≈0.65 → 일간 수익률은 사실상 '시장 베타의 선형결합'. β(SKQ~하이닉스)≈0.85,
    하이닉스가 일간 분산의 ~57% 설명, 나머지 ~43%는 디스카운트發 고유위험.
  · 익일 예측: OOS R²<0, 방향적중 50%대(기준선 이하) → 익일 예측력 없음(효율적 시장).
    이 모듈을 '예측'이 아니라 '설명/귀인'에 쓰는 이유.
  · NAV 디스카운트 일변화: ~95%가 divergence(주가-NAV 괴리)의 기계적 항등식. 일간
    평균회귀 없음. 디스카운트 축소(2023말 ~74% → 2026 ~42%)는 자사주 소각·밸류업의
    구조적 효과로, 일간이 아닌 누적 알파의 원천.
────────────────────────────────────────────────────────────────────────
"""
from pathlib import Path
from loguru import logger

from tools.features import build_features, get_xy, FEATURES, TARGET, FEATURE_TAXONOMY

_BASE = Path(__file__).parent.parent
MIN_DAYS = 250  # XGBoost 비선형 귀인 최소 관측 수 (requirements.txt 기준)

# 트리 깊이는 얕게(과적합 억제) — 소표본·강한 선형신호 특성에 맞춤
XGB_PARAMS = dict(
    n_estimators=400,
    learning_rate=0.05,
    max_depth=3,
    subsample=0.85,
    colsample_bytree=0.9,
    reg_lambda=1.0,
    min_child_weight=5,
    random_state=0,
    n_jobs=4,
    importance_type="gain",
    objective="reg:squarederror",
)


def _imports():
    """무거운 의존성 지연 임포트 (+ 친절한 안내)."""
    try:
        import numpy as np
        import pandas as pd
        import xgboost as xgb
        import shap
        return np, pd, xgb, shap
    except ImportError as e:
        raise ImportError(
            f"필요 패키지 미설치: {e}  →  pip install xgboost shap scikit-learn pandas numpy"
        )


def _make_model(n_estimators=None):
    import xgboost as xgb
    p = dict(XGB_PARAMS)
    if n_estimators:
        p["n_estimators"] = n_estimators
    return xgb.XGBRegressor(**p)


def _cv_r2(X, y, k: int = 4, min_train: float = 0.4):
    """확장창(expanding window) 시계열 교차검증 OOS R². (누수 없음)"""
    import numpy as np
    n = len(y)
    edges = np.linspace(int(n * min_train), n, k + 1).astype(int)
    oof = np.full(n, np.nan)
    for i in range(k):
        a, b = int(edges[i]), int(edges[i + 1])
        if b <= a:
            continue
        m = _make_model()
        m.fit(X.iloc[:a], y.iloc[:a])
        oof[a:b] = m.predict(X.iloc[a:b])
    mask = ~np.isnan(oof)
    yv = y.values[mask]
    pv = oof[mask]
    ss = float(((yv - pv) ** 2).sum())
    st = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1 - ss / st if st > 0 else float("nan")
    return r2, int(mask.sum())


def train(lookback: int = None, fill: str = "ffill", report_cv: bool = True):
    """
    XGBoost 귀인 모델 학습.

    Args:
        lookback: 최근 N거래일만 사용 (None=전 구간)
        fill:     features.get_xy 의 결측 처리 ("ffill" 권장)
        report_cv: 시계열 OOS R² 로깅 여부
    Returns:
        (model, X, y, dates)
    """
    np, pd, xgb, shap = _imports()
    X, y, dates = get_xy(lookback=lookback, fill=fill)
    if len(y) < MIN_DAYS:
        logger.warning(
            f"데이터 {len(y)}거래일 (<{MIN_DAYS}). 귀인 신뢰도 낮음 — "
            f"`python main.py log` 누적 또는 `python main.py backfill` 권장."
        )
    if report_cv and len(y) >= 40:
        r2, n_oos = _cv_r2(X, y)
        logger.info(f"시계열 OOS R²={r2:.3f} (N_oos={n_oos}) — 당일 수익률 설명력")
    model = _make_model()
    model.fit(X, y)
    return model, X, y, dates


def _taxonomy_top(feat: str) -> str:
    code = FEATURE_TAXONOMY.get(feat, ("?", ""))[0]
    return code.split(".")[0]


def global_importance(lookback: int = None, top: int = 15) -> dict:
    """
    글로벌 팩터 중요도: gain + SHAP(mean|abs|), F1~F7 카테고리 귀인 합산.
    Returns dict (gain/shap per feature, category shares).
    """
    np, pd, xgb, shap = _imports()
    model, X, y, dates = train(lookback)

    gain = pd.Series(model.feature_importances_, index=list(X.columns))
    gain = gain / gain.sum() if gain.sum() > 0 else gain

    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X)
    shap_imp = pd.Series(np.abs(sv).mean(axis=0), index=list(X.columns))
    shap_imp = shap_imp / shap_imp.sum() if shap_imp.sum() > 0 else shap_imp

    # 카테고리(F1~F7) 합산 — features.py taxonomy 활용
    cat = {}
    for f in X.columns:
        cat[_taxonomy_top(f)] = cat.get(_taxonomy_top(f), 0.0) + float(shap_imp[f])
    cat = dict(sorted(cat.items(), key=lambda kv: -kv[1]))

    print(f"\n── XGBoost 팩터 중요도 (N={len(y)}거래일"
          f"{'' if lookback is None else f', 최근 {lookback}일'}) ──")
    print("  [SHAP mean|기여|]  상위 피처")
    for f, v in shap_imp.sort_values(ascending=False).head(top).items():
        code = FEATURE_TAXONOMY.get(f, ("?", ""))[0]
        bar = "#" * int(v * 60)
        print(f"   [{code:5}] {f:24} {v:6.3f} {bar}")
    print("\n  [카테고리 귀인]  F1=NAV/하이닉스 F2=섹터 F3=수급 F4=공매도 F5=선물 F7=매크로 F8=국면·상호작용")
    for c, v in cat.items():
        bar = "#" * int(v * 60)
        print(f"   {c:4} {v:6.3f} {bar}")
    print("  (※ 당일 '설명' 관점. 익일 예측력은 별개로 ~0 — docstring 참조)\n")

    return {"gain": gain.to_dict(), "shap": shap_imp.to_dict(), "category": cat,
            "n_obs": int(len(y))}


def explain_day(date: str = None, lookback: int = None) -> dict:
    """
    특정 일자의 SHAP 기여분 분해 — '오늘 주가가 왜 그렇게 움직였나'를 팩터별로 나눔.

    Args:
        date: 'YYYYMMDD' (None이면 가장 최근 거래일)
    Returns:
        {date, actual, base, predicted, contributions(list), category(dict)}
    """
    np, pd, xgb, shap = _imports()
    model, X, y, dates = train(lookback)

    # 설명 대상 행: build_features 전체에서 해당 일자 피처(ffill)로 추출
    full = build_features()
    full[FEATURES] = full[FEATURES].ffill()
    if date:
        d = pd.to_datetime(str(date), format="%Y%m%d")
        sub = full[full["date"] == d]
        if sub.empty:
            raise ValueError(f"{date} 거래일 데이터 없음 (휴장이거나 미적재).")
    else:
        sub = full.dropna(subset=FEATURES).tail(1)
        d = sub["date"].iloc[0]

    row = sub[FEATURES].iloc[[0]]
    if row.isna().any(axis=1).iloc[0]:
        row = row.fillna(0.0)

    explainer = shap.TreeExplainer(model)
    base = float(np.ravel(explainer.expected_value)[0])
    sv = explainer.shap_values(row)[0]
    contrib = sorted(
        [(f, float(c)) for f, c in zip(FEATURES, sv)],
        key=lambda kv: -abs(kv[1]),
    )
    predicted = base + float(np.sum(sv))
    actual = sub[TARGET].iloc[0] if TARGET in sub and pd.notna(sub[TARGET].iloc[0]) else None

    # 카테고리 합산
    cat = {}
    for f, c in contrib:
        cat[_taxonomy_top(f)] = cat.get(_taxonomy_top(f), 0.0) + c
    cat = dict(sorted(cat.items(), key=lambda kv: -abs(kv[1])))

    dstr = d.strftime("%Y-%m-%d")
    print(f"\n── {dstr} SHAP 기여분 분해 ──")
    if actual is not None:
        print(f"  실제 SKQ 수익률  {actual:+.2f}%")
    print(f"  기준값(base)     {base:+.2f}%   →  모델 설명값 {predicted:+.2f}%")
    print(f"  ───  팩터별 기여 (상위, %p)  ───")
    for f, c in contrib[:10]:
        if abs(c) < 1e-4:
            continue
        code = FEATURE_TAXONOMY.get(f, ("?", ""))[0]
        desc = FEATURE_TAXONOMY.get(f, ("", f))[1].split(" [")[0]
        arrow = "▲" if c > 0 else "▼"
        print(f"   {arrow} [{code:5}] {f:22} {c:+.2f}  ({desc})")
    print(f"  ───  카테고리 합산 (%p)  ───")
    for c_, v in cat.items():
        if abs(v) < 1e-3:
            continue
        arrow = "▲" if v > 0 else "▼"
        print(f"   {arrow} {c_:4} {v:+.2f}")
    print()

    return {"date": dstr, "actual": (None if actual is None else float(actual)),
            "base": base, "predicted": predicted,
            "contributions": contrib, "category": cat}


def print_attribution(date: str = None) -> None:
    """main.py 엔트리 — date 있으면 일별 분해, 없으면 글로벌 중요도."""
    try:
        if date:
            explain_day(date)
        else:
            global_importance()
    except (ValueError, FileNotFoundError) as e:
        print(f"\n[귀인 분석 불가] {e}")
    except ImportError as e:
        print(f"\n[패키지 필요] {e}")
    except Exception as e:
        logger.error(f"attribution 오류: {e}")
        print(f"\n[오류] {e}")
