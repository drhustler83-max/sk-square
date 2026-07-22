"""
event_residual.csv 생성
상장 이후 전 거래일에 대해 종가·잔차·국면·매칭된 이벤트를 한 행씩 정리한
Sean 리뷰용 마스터 테이블. data/factor_log.csv + data/events.csv를 소스로 한다.

컬럼
  date           거래일 (YYYYMMDD)
  skq_price      SK스퀘어 종가 (원)
  hynix_price    SK하이닉스 종가 (원)
  regime         동일 / 교차 / Zero (skq_ret·hynix_ret 부호 비교, 결측이면 공백)
  divergence     잔차 (%p, factor_log.divergence)
  high_residual  |divergence| >= HIGH_RESIDUAL_THRESHOLD 인 날 "Y" (그 외 공백)
  event_name     해당 거래일에 매칭된 이벤트명(복수면 " | "로 연결) — source_type이
                 dart/homepage인 이벤트만 포함(애널리스트 인용 등 나머지 원천은 제외).
                 tools.regime_events의 effective_date 규칙(다음 거래일) 매칭 결과.
  direction      (수동 입력용, 현재 비워둠)
  importance     (수동 입력용, 현재 비워둠)
  note           (수동 입력용, 현재 비워둠)
  source         event_name과 동일한 행(들)의 원천 표기(예: "DART 20220128 rcpNo=..."
                 | "SK스퀘어 홈페이지 뉴스룸 20220328"). event_name과 같은 규칙으로
                 events.csv의 source 컬럼을 그룹핑한 값 — 자동 산출.

주의
  event_name은 dart/homepage 두 원천만 반영한다 — 애널리스트 리포트는 이벤트 실제
  발생일과 무관하게 훨씬 뒤에 인용하는 경우가 많아(예: 2024-08 리포트가 2024-02 실적
  발표를 인용) effective_date 매칭이 부정확했던 문제가 있었음. dart/homepage는 공시·
  보도자료 자체이므로 date 컬럼이 실제 발생일과 일치 → "다음 거래일 반영" 규칙 신뢰 가능.

사용
  python -m tools.build_event_residual
"""
import ssl
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

from pathlib import Path
import numpy as np
import pandas as pd
from pykrx import stock
from loguru import logger

from tools.factor_logger import LOG_PATH
from tools.regime_events import load_events_with_effective_date

_BASE = Path(__file__).parent.parent
OUT_PATH = _BASE / "data" / "event_residual.csv"

SKQ_TICKER = "402340"
HYNIX_TICKER = "000660"
HIGH_RESIDUAL_THRESHOLD = 3.0
DISCLOSURE_SOURCE_TYPES = ("dart", "homepage")


def build(save: bool = True) -> pd.DataFrame:
    fl = pd.read_csv(LOG_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    start, end = fl["date"].min().strftime("%Y%m%d"), fl["date"].max().strftime("%Y%m%d")

    skq_close = stock.get_market_ohlcv(start, end, SKQ_TICKER)[["종가"]].rename(columns={"종가": "skq_price"})
    hyx_close = stock.get_market_ohlcv(start, end, HYNIX_TICKER)[["종가"]].rename(columns={"종가": "hynix_price"})
    skq_close.index.name = hyx_close.index.name = "date"

    df = fl[["date", "skq_ret", "hynix_ret", "divergence"]].merge(
        skq_close, on="date", how="left"
    ).merge(hyx_close, on="date", how="left")

    regime = np.where(
        df["skq_ret"].isna() | df["hynix_ret"].isna(), "",
        np.where(
            (df["skq_ret"] == 0) | (df["hynix_ret"] == 0), "Zero",
            np.where(np.sign(df["skq_ret"]) != np.sign(df["hynix_ret"]), "교차", "동일"),
        ),
    )
    df["regime"] = regime
    df["high_residual"] = np.where(df["divergence"].abs() >= HIGH_RESIDUAL_THRESHOLD, "Y", "")

    ev = load_events_with_effective_date()
    ev = ev[ev["source_type"].isin(DISCLOSURE_SOURCE_TYPES)]
    grouped = (
        ev.dropna(subset=["effective_date"])
        .groupby("effective_date")
        .agg(event_name=("event", lambda s: " | ".join(s)), source=("source", lambda s: " | ".join(s)))
        .reset_index()
        .rename(columns={"effective_date": "date"})
    )
    df = df.merge(grouped, on="date", how="left")
    df["event_name"] = df["event_name"].fillna("")
    df["source"] = df["source"].fillna("")

    for col in ("direction", "importance", "note"):
        df[col] = ""

    out = df[[
        "date", "skq_price", "hynix_price", "regime", "divergence", "high_residual",
        "event_name", "direction", "importance", "note", "source",
    ]].copy()
    out["date"] = out["date"].dt.strftime("%Y%m%d")

    if save:
        out.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
        logger.info(f"저장: {OUT_PATH} ({len(out)}행)")

    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(str(_BASE / ".env"))
    result = build()
    print(f"event_residual.csv 생성 완료: {len(result)}행")
    print(f"event_name 매칭된 행: {(result['event_name'] != '').sum()}행")
