"""
Event Study — 정성 이벤트(뉴스·공시) ↔ 잔차(divergence) 분석

배경
  OLS/XGBoost는 숫자 팩터(하이닉스·섹터·매크로·수급·공매도)만 본다. SK스퀘어 고유
  뉴스(자사주·지배구조·밸류업 등)는 모델 입력에 없고, 그 영향은 NAV로 설명 안 되는
  나머지 = divergence(잔차, F7)로 남는다. 도메인 전문가가 손으로 고른 중요 이벤트를
  날짜에 태깅하면, "그 뉴스가 잔차를 실제로 얼마나 움직였나"를 직접 측정할 수 있다.
  (금융 실증분석의 표준: Event Study)

데이터: data/events.csv  (사람이 직접 관리)
  date        YYYYMMDD (거래일)
  category    자사주 / 실적 / 지배구조 / 규제 / 고객사 / 매크로 / 기타 (taxonomy F6 등)
  event       사건 한 줄 설명
  direction   SK스퀘어 잔차에 대한 예상 방향:  +(초과상승) / -(초과하락) / ?(불명)
  importance  H / M / L (또는 1~3)
  note        메모
  source      출처 URL (선택)

사용
  python main.py events                  # 이벤트 vs 비이벤트 잔차 비교 + 건별 상세
  from tools.events import attach_event_features
  df = attach_event_features(build_features())   # event_flag, event_dir 컬럼 추가
"""
from pathlib import Path
from loguru import logger

from tools.features import build_features

EVENTS_PATH = Path(__file__).parent.parent / "data" / "events.csv"

# 잔차 대용치(proxy). 기본은 NAV 기반 잔차 divergence. 나중에 모델 잔차로 교체 가능.
RESID_COL = "divergence"

_DIR_MAP = {"+": 1, "-": -1, "?": 0, "": 0, "nan": 0}


def load_events() -> "pd.DataFrame":
    """data/events.csv 로드 → date(datetime), dir(±1/0) 정규화."""
    import pandas as pd
    if not EVENTS_PATH.exists():
        raise FileNotFoundError(f"이벤트 파일 없음: {EVENTS_PATH}")
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            ev = pd.read_csv(EVENTS_PATH, dtype=str, encoding=enc).fillna("")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise ValueError(f"events.csv 인코딩 인식 실패 (utf-8/cp949 모두 실패): {EVENTS_PATH}")
    ev = ev[ev["date"].str.strip() != ""]
    if ev.empty:
        return ev.assign(date=pd.to_datetime([]), dir=[])
    ev["date"] = pd.to_datetime(ev["date"].str.strip(), format="%Y%m%d", errors="coerce")
    ev = ev.dropna(subset=["date"])
    ev["dir"] = (ev.get("direction", "").astype(str).str.strip()
                 .map(_DIR_MAP).fillna(0).astype(int))
    return ev


def attach_event_features(df: "pd.DataFrame") -> "pd.DataFrame":
    """feature matrix에 event_flag(0/1), event_dir(net ±) 컬럼 추가."""
    ev = load_events()
    df = df.copy()
    if ev.empty:
        df["event_flag"] = 0
        df["event_dir"] = 0
        return df
    dir_by_date = ev.groupby("date")["dir"].sum()  # 하루 다중 이벤트면 방향 합산
    df["event_flag"] = df["date"].isin(ev["date"]).astype(int)
    df["event_dir"] = df["date"].map(dir_by_date).fillna(0).astype(int)
    return df


def residual_event_analysis(resid_col: str = RESID_COL) -> None:
    """이벤트일 vs 비이벤트일 잔차 비교 + 방향 적중률 + 건별 상세 출력."""
    import numpy as np

    ev = load_events()
    if ev.empty:
        print(f"\n[이벤트 없음] {EVENTS_PATH} 가 비어있습니다.")
        print("  date,category,event,direction,importance,note,source 형식으로 채우세요.")
        print("  예) 20240722,자사주,3천억 자사주 매입·소각 발표,+,H,주주환원 강화,https://...")
        return

    df = attach_event_features(build_features())
    d = df.dropna(subset=[resid_col])

    ev_days = d[d["event_flag"] == 1]
    non_days = d[d["event_flag"] == 0]
    if len(ev_days) == 0:
        print(f"\n[매칭 0건] 이벤트 날짜가 factor_log 거래일과 안 맞습니다 "
              f"(휴장일이거나 데이터 범위 밖).")
        return

    ev_abs = ev_days[resid_col].abs().mean()
    non_abs = non_days[resid_col].abs().mean()
    print(f"\n── 이벤트 vs 비이벤트 잔차({resid_col}) 비교 ──")
    print(f"  이벤트일:   {len(ev_days):4}일,  평균 |{resid_col}| = {ev_abs:.2f}%p")
    print(f"  비이벤트일: {len(non_days):4}일,  평균 |{resid_col}| = {non_abs:.2f}%p")
    if non_abs > 0:
        print(f"  → 이벤트일 잔차가 평소의 {ev_abs / non_abs:.1f}배")

    # 방향성 이벤트: 예상 방향과 실제 잔차 부호 일치율
    dirset = ev_days[ev_days["event_dir"] != 0]
    if len(dirset):
        hit = (np.sign(dirset[resid_col]) == np.sign(dirset["event_dir"])).mean()
        print(f"  방향성 이벤트 {len(dirset)}건 중 잔차 부호 일치율: {hit * 100:.0f}%")

    # 건별 상세 — 잔차 큰 순
    ev_full = ev.merge(d[["date", "skq_ret", resid_col]], on="date", how="inner")
    ev_full = ev_full.reindex(ev_full[resid_col].abs().sort_values(ascending=False).index)
    print(f"\n── 이벤트 건별 잔차 (|{resid_col}| 큰 순) ──")
    for _, r in ev_full.head(20).iterrows():
        exp = {1: "+", -1: "-", 0: "?"}[r["dir"]]
        actual = r[resid_col]
        match = "O" if (r["dir"] != 0 and np.sign(actual) == np.sign(r["dir"])) else \
                ("X" if r["dir"] != 0 else " ")
        print(f"  {r['date'].date()} [{r.get('category','')[:6]:6}] "
              f"예상{exp} 실제 {actual:+.2f}%p {match}  {str(r.get('event',''))[:30]}")
    print()
