"""
Factor Beta Calculator
data/factor_log.csv (60거래일 이상)를 읽어 rolling OLS로 팩터 베타 산출.

모델:
  skq_ret = β_nav   × hynix_ret
          + β_sector × sector_semiconductor
          + β_macro  × kospi_ret
          + β_fx     × usd_krw_chg_pct
          + α (intercept)
          + ε

사용:
  python main.py beta
"""
from pathlib import Path
from loguru import logger


MIN_DAYS = 60  # OLS 최소 관측 수


def compute_betas(lookback: int = 60) -> dict:
    """
    최근 lookback 거래일 데이터로 OLS 회귀 → 팩터 베타 반환.

    Returns:
        {
          "beta_nav":    float,
          "beta_sector": float,
          "beta_macro":  float,
          "beta_fx":     float,
          "alpha":       float,
          "r_squared":   float,
          "n_obs":       int,
          "period":      (start_date, end_date),
        }
    """
    try:
        import numpy as np
        import pandas as pd
        from sklearn.linear_model import LinearRegression
        from tools.factor_logger import load_log
    except ImportError as e:
        raise ImportError(f"필요 패키지 미설치: {e}  →  pip install scikit-learn pandas numpy")

    df = load_log()
    df = df.dropna(subset=["skq_ret", "hynix_ret", "sector_semiconductor",
                            "kospi_ret", "usd_krw_chg_pct"])

    if len(df) < MIN_DAYS:
        raise ValueError(
            f"데이터 부족: {len(df)}거래일 (최소 {MIN_DAYS}거래일 필요). "
            f"앞으로 {MIN_DAYS - len(df)}거래일 더 수집하세요."
        )

    recent = df.tail(lookback).copy()

    X = recent[["hynix_ret", "sector_semiconductor", "kospi_ret", "usd_krw_chg_pct"]].values
    y = recent["skq_ret"].values

    model = LinearRegression()
    model.fit(X, y)

    y_pred = model.predict(X)
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    betas = {
        "beta_nav":    round(model.coef_[0], 4),
        "beta_sector": round(model.coef_[1], 4),
        "beta_macro":  round(model.coef_[2], 4),
        "beta_fx":     round(model.coef_[3], 4),
        "alpha":       round(model.intercept_, 4),
        "r_squared":   round(r2, 4),
        "n_obs":       len(recent),
        "period":      (str(recent["date"].iloc[0].date()),
                        str(recent["date"].iloc[-1].date())),
    }

    logger.info(
        f"OLS 베타 산출 ({betas['period'][0]} ~ {betas['period'][1]}, N={betas['n_obs']})\n"
        f"  β_nav={betas['beta_nav']}, β_sector={betas['beta_sector']}, "
        f"β_macro={betas['beta_macro']}, β_fx={betas['beta_fx']}, "
        f"α={betas['alpha']}, R²={betas['r_squared']}"
    )
    return betas


def print_betas(lookback: int = 60) -> None:
    """콘솔에 베타 결과 출력"""
    try:
        b = compute_betas(lookback)
        print(f"\n── 팩터 베타 (최근 {b['n_obs']}거래일, {b['period'][0]}~{b['period'][1]}) ──")
        print(f"  β_nav    (SK하이닉스 수익률):  {b['beta_nav']:+.4f}")
        print(f"  β_sector (반도체 섹터):        {b['beta_sector']:+.4f}")
        print(f"  β_macro  (KOSPI):              {b['beta_macro']:+.4f}")
        print(f"  β_fx     (USD/KRW 변화율):     {b['beta_fx']:+.4f}")
        print(f"  α        (intercept):           {b['alpha']:+.4f}")
        print(f"  R²                              {b['r_squared']:.4f}")
        print()
    except ValueError as e:
        print(f"\n[베타 산출 불가] {e}")
    except Exception as e:
        print(f"\n[오류] {e}")
