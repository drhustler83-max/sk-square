"""
Feature Extraction
data/factor_log.csv (raw 팩터 로그) → 파생 피처를 더한 feature matrix 생성.

설계 철학
  - raw 레벨 피처(factor_log 원본 컬럼) + 파생 피처(lag / rolling / ratio / flag / interaction)
  - 각 피처는 F1~F7 taxonomy 노드에 매핑(FEATURE_TAXONOMY) → 나중에 XGBoost·SHAP에서
    "어느 카테고리가 기여했나"로 바로 묶을 수 있음.
  - look-ahead(미래 누설) 방지: rolling·shift는 모두 과거+당일만 사용(pandas 기본 trailing).
  - 타깃(label) = skq_ret. 이건 "오늘 무엇이 움직였나"를 설명하는 attribution 관점이라
    파생 피처도 기본은 당일 값. 단 수급은 익일 반영 경향이 있어 lag 피처를 별도로 둠.

사용
  python main.py features                 # feature matrix 요약 + 타깃 상관관계 출력
  from tools.features import build_features, get_xy
  df = build_features()                   # date + raw + 파생 + target 전체
  X, y, dates = get_xy(lookback=60)       # 모델 입력용 (X, y) 분리
"""
from pathlib import Path
from loguru import logger

from tools.factor_logger import load_log, LOG_PATH

TARGET = "skq_ret"

# 선물 로그(별도 cadence) — build_features에서 date 기준 left-join
FUT_LOG_PATH = LOG_PATH.parent / "futures_log.csv"
# 추가 원천(외국인 지분율·개인 순매수) — tools/backfill_extra.py로 생성
EXTRA_LOG_PATH = LOG_PATH.parent / "extra_log.csv"

# ─────────────────────────────────────────────────────────────────────────────
# 피처 ↔ taxonomy 매핑.
#   key = feature 컬럼명, value = (taxonomy 코드, 한 줄 설명)
#   raw  = factor_log 원본 컬럼,  derived = 본 모듈에서 생성
# ─────────────────────────────────────────────────────────────────────────────
FEATURE_TAXONOMY: dict[str, tuple[str, str]] = {
    # ── F1 NAV (가장 중요) ────────────────────────────────────────────────
    "hynix_ret":            ("F1", "SK하이닉스 수익률 (NAV 주동인) [raw]"),
    "nav_discount_pct":     ("F1.3", "NAV 할인율 레벨 [raw]"),
    "nav_discount_delta":   ("F1.3", "NAV 할인율 전일 대비 변화 [raw]"),
    "divergence":           ("F1.4", "SKQ - NAV기반 기대수익률 (잔차) [raw]"),
    "divergence_ma5":       ("F1.4", "divergence 5일 이동평균 (추세) [derived]"),
    "divergence_lag1":      ("F1.4", "전일 divergence (모멘텀 지속성) [derived]"),
    "nav_discount_vs_ma20": ("F1.3", "할인율 - 20일평균 (역사적 위치) [derived]"),
    "nav_discount_z60":     ("F1.3", "할인율 60일 z-score (국면) [derived]"),
    # ── F2 섹터·매크로 ────────────────────────────────────────────────────
    "sector_semiconductor": ("F2", "반도체 섹터 ETF 평균 수익률 [raw]"),
    "kospi_ret":            ("F2", "KOSPI 수익률 (시장 베타) [raw]"),
    "usd_krw_chg_pct":      ("F2", "USD/KRW 변화율 (FX) [raw]"),
    "sector_excess":        ("F2", "반도체 - KOSPI (섹터 초과 강도) [derived]"),
    "hynix_ex_sector":      ("F2", "하이닉스 - 반도체섹터 (종목 고유 알파) [derived]"),
    "kospi_vol20":          ("F2", "KOSPI 수익률 20일 변동성 (시장 변동성 regime) [derived]"),
    # ── F3 수급 ───────────────────────────────────────────────────────────
    "foreign_net":          ("F3", "외국인 순매수 [raw]"),
    "institution_net":      ("F3", "기관 순매수 [raw]"),
    "individual_net":       ("F3", "개인 순매수 (거래대금) [raw]"),
    "flow_sum":             ("F3", "외국인+기관 합산 (큰손 수급) [derived]"),
    "flow_z20":             ("F3", "수급(외국인+기관) 20일 z-score (서프라이즈) [derived]"),
    "foreign_net_3d":       ("F3", "외국인 순매수 3일 누적 [derived]"),
    "foreign_net_lag1":     ("F3", "전일 외국인 순매수 (익일 반영) [derived]"),
    "foreign_own_pct":      ("F3", "외국인 지분율 % (T+1 공시) [raw]"),
    "foreign_own_chg5":     ("F3", "외국인 지분율 5거래일 변화(%p) [derived]"),
    # ── F4 공매도 ─────────────────────────────────────────────────────────
    "shorting_balance_ratio":  ("F4", "공매도 잔고율 [raw]"),
    "shorting_volume_ratio":   ("F4", "당일 공매도 비중 [raw]"),
    "shorting_balance_change": ("F4", "공매도 잔고 전일 대비 변화 [raw]"),
    "short_bal_chg_3d":        ("F4", "공매도 잔고 변화 3일 누적 [derived]"),
    "short_ratio_vs_ma20":     ("F4", "잔고율 - 20일평균 (국면) [derived]"),
    "short_pressure_flag":     ("F4", "공매도 압력 국면 플래그(증가&잔고율≥1%) [derived]"),
    # ── F5 파생(선물) ─────────────────────────────────────────────────────
    #   상장이 간헐적이라 미상장 구간은 0으로 채우고 fut_listed로 구분
    "fut_listed":           ("F5", "주식선물 상장 여부 0/1 (미상장 구간 다수) [raw]"),
    "fut_basis_pct":        ("F5", "근월물 베이시스 %=(선물-현물)/현물, 콘탱고+/백워데이션- (미상장=0) [raw]"),
    "fut_volume":           ("F5", "근월물 거래량 (미상장=0) [raw]"),
    "fut_basis_chg":        ("F5", "선물 베이시스 전일 대비 변화(%p) (미상장=0) [derived]"),
    "fut_volume_z":         ("F5", "선물 거래량 20일 z-score (비정상 거래) (미상장=0) [derived]"),
    # ── 국면·상호작용 (F7 잔차 탐색용) ─────────────────────────────────────
    "skq_vol20":            ("F7", "SKQ 수익률 20일 변동성 (위험 국면) [derived]"),
    "hynix_x_shortinc":     ("F7", "하이닉스수익률 × 공매도증가 (상호작용) [derived]"),
    "disc_delta_lag1":      ("F7", "전일 NAV 할인율 변화 (비누설 모멘텀) [derived]"),
}

FEATURES: list[str] = list(FEATURE_TAXONOMY.keys())


def _attach_futures(df: "pd.DataFrame") -> "pd.DataFrame":
    """data/futures_log.csv를 date 기준 left-join하여 F5 파생(선물) 피처 추가.

    선물은 상장 구간이 간헐적(미상장 구간 다수)이라 다음 규칙으로 정규화한다.
      - fut_listed(0/1) 플래그로 '상장 중 베이시스 0'과 '미상장(0)'을 구분.
      - 미상장/결측일의 basis·volume은 0으로 채운다 → 이렇게 안 하면 NaN이 남아
        get_xy의 dropna(FEATURES)가 미상장 거래일(전체의 상당수)을 통째로 날린다.
      - rolling/MA는 의도적으로 쓰지 않음 — 상장/미상장 경계를 가로질러 오염되므로.
        추세·국면화는 XGBoost가 fut_listed와의 상호작용으로 학습하도록 둔다.
    """
    import pandas as pd

    if not FUT_LOG_PATH.exists():
        logger.warning(f"선물 로그 없음: {FUT_LOG_PATH} — F5 피처를 0으로 채움")
        df["fut_listed"] = 0
        df["fut_basis_pct"] = 0.0
        df["fut_volume"] = 0.0
        return df

    fut = pd.read_csv(FUT_LOG_PATH, parse_dates=["date"])
    keep = [c for c in ["date", "fut_listed", "fut_basis_pct", "fut_volume"] if c in fut.columns]
    df = df.merge(fut[keep], on="date", how="left")

    df["fut_listed"]    = (pd.to_numeric(df["fut_listed"], errors="coerce").fillna(0) > 0).astype(int)
    df["fut_basis_pct"] = pd.to_numeric(df["fut_basis_pct"], errors="coerce").fillna(0.0)
    df["fut_volume"]    = pd.to_numeric(df["fut_volume"], errors="coerce").fillna(0.0)
    # 미상장일은 선물 신호가 없음 → basis/volume 0으로 강제(플래그로 구분 가능)
    df.loc[df["fut_listed"] == 0, ["fut_basis_pct", "fut_volume"]] = 0.0
    return df


def _attach_extra(df: "pd.DataFrame") -> "pd.DataFrame":
    """data/extra_log.csv를 date 기준 left-join → 외국인 지분율·개인 순매수 raw 컬럼 추가."""
    import pandas as pd

    if not EXTRA_LOG_PATH.exists():
        logger.warning(f"extra 로그 없음: {EXTRA_LOG_PATH} — foreign_own_pct/individual_net NaN")
        df["foreign_own_pct"] = float("nan")
        df["individual_net"] = float("nan")
        return df

    ex = pd.read_csv(EXTRA_LOG_PATH, parse_dates=["date"])
    keep = [c for c in ["date", "foreign_own_pct", "individual_net"] if c in ex.columns]
    df = df.merge(ex[keep], on="date", how="left")
    df["foreign_own_pct"] = pd.to_numeric(df["foreign_own_pct"], errors="coerce")
    df["individual_net"]  = pd.to_numeric(df["individual_net"], errors="coerce")
    return df


def build_features() -> "pd.DataFrame":
    """
    factor_log 전체(상장 이후 전 구간)를 읽어 파생 피처를 추가한 DataFrame 반환.

    파생 피처는 전 구간 기준으로 계산(rolling 60 등이 초반 행에서 NaN) 후,
    슬라이싱/dropna는 get_xy 또는 호출측에서 수행.

    Returns:
        columns = ["date"] + FEATURES + [TARGET] (+ factor_log 원본 컬럼 일부)
    """
    df = load_log()  # date(datetime) 정렬, usd_krw_chg_pct 포함
    df = _attach_extra(df)  # 외국인 지분율·개인 순매수 raw 컬럼 추가

    # rolling 창 안에 흩어진 결측(연초·휴장경계의 NaN) 1~2개가 전체 결과를 NaN으로
    # 전파하지 않도록 min_periods를 둔다. (factor_log엔 skq_ret 등 결측이 ~24개 산재)

    # ── F1 NAV 파생 ───────────────────────────────────────────────────────
    df["divergence_ma5"]       = df["divergence"].rolling(5, min_periods=3).mean()
    df["divergence_lag1"]      = df["divergence"].shift(1)
    disc_ma20                  = df["nav_discount_pct"].rolling(20, min_periods=10).mean()
    df["nav_discount_vs_ma20"] = df["nav_discount_pct"] - disc_ma20
    disc_mean60                = df["nav_discount_pct"].rolling(60, min_periods=30).mean()
    disc_std60                 = df["nav_discount_pct"].rolling(60, min_periods=30).std()
    df["nav_discount_z60"]     = (df["nav_discount_pct"] - disc_mean60) / disc_std60.replace(0, float("nan"))

    # ── F2 섹터·매크로 파생 ───────────────────────────────────────────────
    df["sector_excess"]   = df["sector_semiconductor"] - df["kospi_ret"]
    df["hynix_ex_sector"] = df["hynix_ret"] - df["sector_semiconductor"]
    df["kospi_vol20"]     = df["kospi_ret"].rolling(20, min_periods=10).std()  # 시장 변동성 regime (VIX 대용)

    # ── F3 수급 파생 ──────────────────────────────────────────────────────
    df["flow_sum"]        = df["foreign_net"] + df["institution_net"]
    df["foreign_net_3d"]  = df["foreign_net"].rolling(3, min_periods=2).sum()
    df["foreign_net_lag1"] = df["foreign_net"].shift(1)
    # 수급 서프라이즈: flow_sum의 20일 z-score (당일 수급이 평소 대비 얼마나 이례적인가)
    _flow_mean = df["flow_sum"].rolling(20, min_periods=10).mean()
    _flow_std  = df["flow_sum"].rolling(20, min_periods=10).std()
    df["flow_z20"] = (df["flow_sum"] - _flow_mean) / _flow_std.replace(0, float("nan"))
    # 외국인 지분율 추세(누적 비중 변화) — T+1 레벨이라 비누설
    df["foreign_own_chg5"] = df["foreign_own_pct"].diff(5)

    # ── F4 공매도 파생 ────────────────────────────────────────────────────
    df["short_bal_chg_3d"]    = df["shorting_balance_change"].rolling(3, min_periods=2).sum()
    short_ratio_ma20          = df["shorting_balance_ratio"].rolling(20, min_periods=10).mean()
    df["short_ratio_vs_ma20"] = df["shorting_balance_ratio"] - short_ratio_ma20
    df["short_pressure_flag"] = (
        (df["shorting_balance_change"] > 0) & (df["shorting_balance_ratio"] >= 1.0)
    ).astype(int)

    # ── 국면·상호작용 ─────────────────────────────────────────────────────
    df["skq_vol20"]       = df["skq_ret"].rolling(20, min_periods=10).std()
    df["hynix_x_shortinc"] = df["hynix_ret"] * (df["shorting_balance_change"] > 0).astype(int)
    df["disc_delta_lag1"] = df["nav_discount_delta"].shift(1)  # 전일 할인율 변화 = 비누설 재평가 모멘텀

    # ── F5 파생(선물) — 별도 로그를 date 기준 left-join ───────────────────
    df = _attach_futures(df)
    # 선물 파생: 베이시스 전일변화 / 거래량 z-score (상장 구간에서만, 미상장=0)
    df["fut_basis_chg"] = df["fut_basis_pct"].diff().fillna(0.0)
    _fv_mean = df["fut_volume"].rolling(20, min_periods=10).mean()
    _fv_std  = df["fut_volume"].rolling(20, min_periods=10).std()
    df["fut_volume_z"] = ((df["fut_volume"] - _fv_mean) / _fv_std.replace(0, float("nan"))).fillna(0.0)
    df.loc[df["fut_listed"] == 0, ["fut_basis_chg", "fut_volume_z"]] = 0.0

    return df


def get_xy(lookback: int = None, fill: str = None, dropna: bool = True):
    """
    모델 입력용 (X, y, dates) 반환.

    Args:
        lookback: 최근 N거래일만 사용(None이면 전 구간).
                  파생 피처는 전 구간 기준으로 먼저 계산하므로 tail(N)도 유효.
        fill:     None  → 채우지 않음(결측 행은 dropna로 제거).
                  "ffill" → 피처를 직전 값으로 전진 채움(forward fill).
                  ※ 타깃(skq_ret)은 어떤 경우에도 채우지 않는다 — 없는 수익률을
                    만들어내면 학습 누설이 되므로, 타깃 결측 행은 항상 제거.
                  ※ ffill은 레벨형(공매도 잔고·환율·할인율 등)에 타당하다. 수익률·플로우
                    컬럼의 결측은 대부분 연초·휴장경계 행인데, 그 행은 타깃도 없어
                    어차피 제거되므로 모델 입력엔 영향이 거의 없다.
        dropna:   채우기 후에도 남은 결측(선행 행 등) 제거.
    Returns:
        (X: DataFrame[FEATURES], y: Series[TARGET], dates: Series[date])
    """
    df = build_features()

    if fill == "ffill":
        # 전 구간 기준으로 먼저 채운 뒤 슬라이싱(슬라이스 경계 너머로 carry되도록)
        df[FEATURES] = df[FEATURES].ffill()

    if lookback:
        df = df.tail(lookback)

    # 타깃 결측은 항상 제거 (절대 채우지 않음)
    df = df.dropna(subset=[TARGET])
    if dropna:
        df = df.dropna(subset=FEATURES)

    return df[FEATURES].copy(), df[TARGET].copy(), df["date"].copy()


def feature_summary(lookback: int = None) -> None:
    """콘솔에 feature matrix 현황 + 타깃 상관관계 출력 (점검용)."""
    df = build_features()
    n_total = len(df)

    complete = df.dropna(subset=FEATURES + [TARGET])
    n_complete = len(complete)

    # ffill 적용 시 회복되는 행 수 (타깃 결측은 항상 제거)
    df_f = df.copy()
    df_f[FEATURES] = df_f[FEATURES].ffill()
    n_complete_ffill = len(df_f.dropna(subset=FEATURES + [TARGET]))

    print(f"\n── Feature Matrix 현황 ──")
    print(f"  전체 거래일:        {n_total}행")
    print(f"  완전관측(dropna):    {n_complete}행  (결측행 {n_total - n_complete} 제거)")
    print(f"  완전관측(ffill 후):  {n_complete_ffill}행  (직전값 채움으로 +{n_complete_ffill - n_complete}행 회복, 타깃 결측은 유지 제거)")
    if n_complete:
        print(f"  기간:               {complete['date'].iloc[0].date()} ~ {complete['date'].iloc[-1].date()}")
    print(f"  피처 수:            {len(FEATURES)}개  (raw + derived)")

    # taxonomy 그룹별 피처 수
    groups: dict[str, int] = {}
    for code, _ in FEATURE_TAXONOMY.values():
        top = code.split(".")[0]
        groups[top] = groups.get(top, 0) + 1
    print("  taxonomy 분포:      " + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())))

    # 결측 진단 — 완전관측 행 수를 깎아먹는 '범인' 피처 찾기
    na = df[FEATURES].isna().sum().sort_values(ascending=False)
    sparse = na[na > 0]
    if len(sparse):
        print(f"\n── 결측 상위 피처 (전체 {n_total}행 기준) ──")
        for name, cnt in sparse.head(10).items():
            code = FEATURE_TAXONOMY[name][0]
            print(f"  [{code:5}] {name:24} NaN {cnt:4}행 ({cnt / n_total * 100:.0f}%)")

    # 타깃(skq_ret)과의 상관 — 어떤 피처가 '신호'를 갖는지 1차 점검
    use = complete if lookback is None else complete.tail(lookback)
    if len(use) >= 10:
        corr = use[FEATURES + [TARGET]].corr()[TARGET].drop(TARGET)
        corr = corr.reindex(corr.abs().sort_values(ascending=False).index)
        scope = "전 구간" if lookback is None else f"최근 {lookback}일"
        print(f"\n── 타깃(skq_ret) 상관관계 상위 ({scope}, N={len(use)}) ──")
        for name, c in corr.head(12).items():
            code = FEATURE_TAXONOMY[name][0]
            bar = "#" * int(abs(c) * 20)
            print(f"  [{code:5}] {name:24} {c:+.3f} {bar}")
        print("  (※ 상관은 선형 1차 점검용 - 비선형·상호작용은 XGBoost+SHAP 단계에서)")
    print()
