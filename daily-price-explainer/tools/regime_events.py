"""
Regime x Event Merge
잔차(divergence) 국면(동일/교차/zero)별 테이블 + 이벤트 유효일(effective_date) 매칭.

배경
  기존 events.csv는 DART/홈페이지/애널리스트 3개 원천이 뒤섞여 있고 날짜 배정이
  일관되지 않다. 대신 "잔차가 큰 날을 먼저 찾고 그 날짜에 걸리는 이벤트를 역으로
  찾는" 방향으로 전환 — 이 모듈은 그 매칭의 시차(lag) 규칙을 담당한다.

effective_date 규칙 (source_type별 시장 반영일)
  - DART 공시 / 홈페이지 보도자료: 장 마감 후 발생 → 다음 거래일에 반영
  - 애널리스트 리포트: 장 시작 전(오전 9시 이전) 발간 → 당일 반영
    (이벤트 date가 비거래일이면 다음 거래일로 롤)
  - 뉴스매체 링크·source_type 불명(unknown): 발간 시각을 알 수 없으므로
    homepage와 동일 규칙(다음 거래일)을 적용하되 date_confidence="assumed"로
    표시 — 수동 검토 대상.

사용
  from tools.regime_events import build_all
  tables = build_all()   # {"same": df, "cross": df, "zero": df} + output/*.csv 저장
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

_BASE = Path(__file__).parent.parent
FACTOR_LOG = _BASE / "data" / "factor_log.csv"
EVENTS_PATH = _BASE / "data" / "events.csv"
OUT_DIR = _BASE / "output"

REGIME_COLS = ["date", "skq_ret", "hynix_ret", "divergence", "nav_discount_pct", "nav_discount_delta"]

# source 문자열 → source_type 분류 규칙 (순서 중요: 위에서부터 첫 매치)
_SOURCE_PATTERNS = [
    (re.compile(r"^DART"), "dart"),
    (re.compile(r"홈페이지"), "homepage"),
    (re.compile(r"sksquare\.com"), "homepage"),
    (re.compile(r"리포트"), "analyst"),
    (re.compile(r"^https?://"), "news"),
]


def classify_source(source: str) -> str:
    s = (source or "").strip()
    for pat, label in _SOURCE_PATTERNS:
        if pat.search(s):
            return label
    return "unknown"


def load_valid_factor() -> pd.DataFrame:
    """factor_log에서 skq_ret/hynix_ret/divergence 모두 있는 행만, regime 컬럼 부여."""
    df = pd.read_csv(FACTOR_LOG, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    valid = df.dropna(subset=["skq_ret", "hynix_ret", "divergence"]).copy()
    valid["regime"] = np.where(
        (valid["skq_ret"] == 0) | (valid["hynix_ret"] == 0), "zero",
        np.where(np.sign(valid["skq_ret"]) != np.sign(valid["hynix_ret"]), "cross", "same"),
    )
    return valid


def split_regime_tables(valid: pd.DataFrame = None) -> dict:
    """동일/교차/zero 3개 테이블로 분리, 각 날짜순 정렬."""
    if valid is None:
        valid = load_valid_factor()
    return {
        regime: valid.loc[valid["regime"] == regime, REGIME_COLS]
        .sort_values("date")
        .reset_index(drop=True)
        for regime in ("same", "cross", "zero")
    }


def load_events_with_effective_date() -> pd.DataFrame:
    """events.csv 로드 → source_type 분류 → effective_date(시장 반영일) 부여."""
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            ev = pd.read_csv(EVENTS_PATH, dtype=str, encoding=enc).fillna("")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise ValueError(f"events.csv 인코딩 인식 실패: {EVENTS_PATH}")

    ev = ev[ev["date"].str.strip() != ""].copy()
    ev["date"] = pd.to_datetime(ev["date"].str.strip(), format="%Y%m%d", errors="coerce")
    ev = ev.dropna(subset=["date"]).reset_index(drop=True)
    ev["source_type"] = ev["source"].map(classify_source)

    trading_days = pd.DatetimeIndex(
        pd.read_csv(FACTOR_LOG, parse_dates=["date"])["date"].sort_values().unique()
    )

    def next_trading_day(d):
        idx = trading_days.searchsorted(d, side="right")
        return trading_days[idx] if idx < len(trading_days) else pd.NaT

    def same_or_next_trading_day(d):
        idx = trading_days.searchsorted(d, side="left")
        return trading_days[idx] if idx < len(trading_days) else pd.NaT

    eff_dates, confidences = [], []
    for st, d in zip(ev["source_type"], ev["date"]):
        if st in ("dart", "homepage"):
            eff_dates.append(next_trading_day(d))
            confidences.append("rule")
        elif st == "analyst":
            eff_dates.append(same_or_next_trading_day(d))
            confidences.append("rule")
        else:  # news, unknown — 발간 시각 불명, homepage 규칙을 잠정 적용
            eff_dates.append(next_trading_day(d))
            confidences.append("assumed")

    ev["effective_date"] = eff_dates
    ev["date_confidence"] = confidences
    return ev


def merge_events_into_regime(tables: dict, ev: pd.DataFrame) -> dict:
    """effective_date 기준으로 이벤트를 regime 테이블에 좌측 조인(하루 다중 이벤트는 집계)."""
    grouped = (
        ev.groupby("effective_date")
        .agg(
            event_count=("event", "count"),
            event_titles=("event", lambda s: " | ".join(s)),
            source_types=("source_type", lambda s: ",".join(sorted(set(s)))),
        )
        .reset_index()
        .rename(columns={"effective_date": "date"})
    )

    merged = {}
    for regime, df in tables.items():
        m = df.merge(grouped, on="date", how="left")
        m["event_count"] = m["event_count"].fillna(0).astype(int)
        m["event_titles"] = m["event_titles"].fillna("")
        m["source_types"] = m["source_types"].fillna("")
        merged[regime] = m
    return merged


def build_all(save: bool = True) -> dict:
    """전체 파이프라인 실행: regime 분리 → 이벤트 effective_date 계산 → 병합.

    Returns:
        {"same": df, "cross": df, "zero": df}  (이벤트 컬럼 포함)
    저장(save=True):
        output/regime_same.csv, regime_cross.csv, regime_zero.csv
        output/events_with_effective_date.csv
    """
    tables = split_regime_tables()
    ev = load_events_with_effective_date()
    merged = merge_events_into_regime(tables, ev)

    if save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for regime, df in merged.items():
            path = OUT_DIR / f"regime_{regime}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"저장: {path} ({len(df)}행)")
        ev_path = OUT_DIR / "events_with_effective_date.csv"
        ev.to_csv(ev_path, index=False, encoding="utf-8-sig")
        logger.info(f"저장: {ev_path} ({len(ev)}행)")

    return merged
