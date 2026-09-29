"""
NAV v2 공통 계산 — data/nav_daily.csv 기반 point-in-time NAV.

tools/apply_nav_v2.py(과거 일괄 보정)와 tools/factor_logger.py(매일 실시간
적재)가 이 모듈의 compute()를 함께 쓴다. 계산 로직을 한 곳에만 두어, 과거
보정과 실시간 적재가 서로 다른 결과를 내는 일이 없게 한다.

get_live_nav_v2(date)는 factor_logger 전용 진입점이다.
  1. listed_holdings_daily.csv / nav_daily.csv 가 date 를 포함하도록 필요하면
     다시 만든다(build_listed_holdings.build 재실행 — 기존 스크립트와 같은
     방식으로 전체 재계산이다, 증분 아님). 하루 한 번 도는 스케줄러 빈도에서는
     감내 가능하나 느리다는 점은 알아둘 것 — 나중에 증분 방식으로 최적화 여지.
  2. 당일 보유 중인 국내 종목의 종가가 실제 그날 고시된 것인지 확인한다
     (reindex+ffill 로 채워진 전일가를 정상 시세로 오인하지 않기 위함).
  3. compute() 로 date 행을 뽑아 반환한다.
  4. 위 어느 단계든 불충분하면 NavDataUnavailable 을 던진다. 호출부(로거)는
     이걸 삼켜 "그날 저장 보류 + 사유 기록"으로만 처리해야 하고, 절대 빈
     값이나 구버전(company_context.py 고정 스냅샷) 계산으로 대신 채워
     저장하면 안 된다 — 그러면 2026-09-22 사고와 다른 방식으로 같은 문제
     (v1/v2 값이 섞여 들어감)가 재발한다.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

LISTED_CSV = BASE / "data" / "listed_holdings_daily.csv"
NAV_DAILY = BASE / "data" / "nav_daily.csv"


class NavDataUnavailable(Exception):
    """그 날짜의 NAV v2 값을 신뢰 가능하게 계산할 수 없음.

    factor_logger는 이 예외를 받으면 그 날짜 전체를 저장하지 않고 사유만
    기록해야 한다(부분 저장·v1 대체 계산 금지).
    """


def compute(nav: pd.DataFrame, skq_ret_ref: pd.Series) -> pd.DataFrame:
    """nav_daily 행렬 → factor_log 용 NAV v2 컬럼.

    tools/apply_nav_v2.py에서 옮겨온 순수 계산 함수 — 파일을 읽거나 쓰지
    않는다. 인자로 받은 nav만 보고 계산하므로 과거 일괄 보정과 매일 실시간
    적재 양쪽에서 동일한 결과를 낸다.

    nav_implied_ret 산출 규칙 — 분기 계단 제거
      비상장·순현금은 분기 경계에서 계단으로 바뀌므로, 그대로 차분하면 분기
      첫날마다 가짜 수익률 스파이크가 생긴다. 수익률은 시가평가 변동분만으로
      계산한다: nav_prev_adj(t) = 상장가치(t-1) + 비상장(t) + 순현금(t)
    """
    nav = nav.sort_values("date").reset_index(drop=True)

    close = nav["skq_close"].astype(float)
    skq_ret = ((close / close.shift(1) - 1.0) * 100.0).round(2)
    ref = pd.to_numeric(skq_ret_ref, errors="coerce")
    both = skq_ret.notna() & ref.notna()
    if both.any():
        gap = (skq_ret[both] - ref[both]).abs()
        logger.info(f"skq_ret 대조: 공통 {int(both.sum())}일, "
                    f"최대차 {gap.max():.3f}%p, 0.01 초과 {int((gap > 0.01).sum())}일")

    listed = nav["listed_value_mtm"].astype(float)
    unl = nav["unlisted_value"].astype(float)
    cash = nav["netcash_value"].astype(float)
    total = nav["nav_mtm"].astype(float)

    prev_adj = listed.shift(1) + unl + cash
    implied = (total / prev_adj - 1.0) * 100.0
    implied_raw = (total / total.shift(1) - 1.0) * 100.0  # 참고용: 계단 포함 원시 차분

    disc = nav["nav_discount_pct_mtm"].astype(float)

    out = pd.DataFrame({
        "date": nav["date"].astype(str),
        "nav_total_trillion": (total / 1e12).round(2),
        "nav_implied_ret": implied.round(2),
        "nav_implied_ret_raw": implied_raw.round(2),
        "nav_discount_pct": disc.round(1),
        "nav_discount_delta": disc.diff().round(1),
        "nav_per_share": nav["nav_per_share_mtm"],
        "skq_shares_v2": nav["skq_shares"],
        "skq_ret_v2": skq_ret,
    })
    out["divergence"] = (skq_ret - out["nav_implied_ret"]).round(2)
    return out


def _current_coverage_end() -> str | None:
    """nav_daily.csv에 현재 들어 있는 마지막 날짜. 파일 없으면 None."""
    if not NAV_DAILY.exists():
        return None
    last = None
    with open(NAV_DAILY, encoding="utf-8-sig") as f:
        for last in csv.DictReader(f):
            pass
    return last["date"] if last else None


def _ensure_coverage(date: str) -> None:
    """nav_daily.csv가 date를 포함하지 않으면 파이프라인을 date까지 다시
    만든다. 이미 date까지 있으면 아무것도 하지 않는다(매일 스케줄러가 돌면
    보통 이 경로 — 어제까지 있고 오늘 하루만 부족).
    """
    end = _current_coverage_end()
    if end is not None and end >= date:
        return

    logger.info(f"nav_daily.csv 커버리지 {end!r} → {date} 로 확장 필요, 파이프라인 재실행")
    from tools.build_listed_holdings import build as build_listed
    from tools.build_nav_daily import build as build_nav

    build_listed(end=date)
    build_nav()


def _check_price_freshness(date: str) -> None:
    """당일 보유 중인 국내 종목의 종가가 실제 그날 고시된 것인지 확인.

    listed_holdings_daily.csv는 reindex+ffill로 결측을 메우므로, 완성된
    파일만 봐서는 전일가가 그대로 들어간 것인지 구분할 수 없다. 원본 시세를
    다시 조회해 당일자 데이터가 실제로 있는지 직접 확인한다.

    IONQ는 검사 대상에서 제외한다 — 설계상 의도적으로 전일 미국 종가를
    쓰므로(한국 장마감 시점엔 당일 미국 장이 아직 안 열림, look-ahead 방지)
    "당일 데이터 없음"이 정상 상태다.
    """
    from tools.build_listed_holdings import HOLDINGS, KR_STOCKS
    from pykrx import stock

    d = pd.Timestamp(date)
    stale = []
    for key, name, ticker in KR_STOCKS:
        shares = 0
        for start, end_, qty, _why, _conf in HOLDINGS[key]:
            s = pd.Timestamp(start)
            e = pd.Timestamp(end_) if end_ else d
            if s <= d <= e:
                shares = qty
        if shares <= 0:
            continue
        try:
            ohlcv = stock.get_market_ohlcv(date, date, ticker)
        except Exception as exc:
            raise NavDataUnavailable(f"{date}: {name}({ticker}) 시세 조회 실패 — {exc}") from exc
        if ohlcv is None or ohlcv.empty:
            stale.append(f"{name}({ticker})")

    if stale:
        raise NavDataUnavailable(
            f"{date}: 보유 중인 종목의 당일 시세 없음(휴장·미수신 등) — {', '.join(stale)}. "
            "전일가로 채운 값을 정상 NAV로 저장하지 않기 위해 이 날짜 적재를 보류합니다."
        )


def _hynix_ret(date: str) -> float | None:
    """listed_holdings_daily.csv의 skhynix_price로 하이닉스 당일 수익률 계산."""
    if not LISTED_CSV.exists():
        return None
    lf = pd.read_csv(LISTED_CSV, dtype={"date": str})
    lf = lf.sort_values("date").reset_index(drop=True)
    if date not in set(lf["date"]):
        return None
    pos = int(lf.index[lf["date"] == date][0])
    if pos == 0:
        return None
    today = float(lf.loc[pos, "skhynix_price"])
    prev = float(lf.loc[pos - 1, "skhynix_price"])
    if prev == 0:
        return None
    return round((today / prev - 1.0) * 100.0, 2)


def get_live_nav_v2(date: str) -> dict:
    """factor_logger 전용 진입점.

    date의 NAV v2 값을 반환하거나 NavDataUnavailable을 던진다. 호출부는
    예외 발생 시 그 날짜를 저장하지 않고 사유만 기록해야 한다.
    """
    _ensure_coverage(date)
    _check_price_freshness(date)

    if not NAV_DAILY.exists():
        raise NavDataUnavailable(f"{NAV_DAILY} 없음")

    nav = pd.read_csv(NAV_DAILY, dtype={"date": str})
    nav = nav.sort_values("date").reset_index(drop=True)

    if date not in set(nav["date"]):
        raise NavDataUnavailable(
            f"{date}: nav_daily.csv에 해당 거래일이 없습니다(파이프라인 확장 실패 또는 휴장일)"
        )

    pos = int(nav.index[nav["date"] == date][0])
    if pos == 0:
        raise NavDataUnavailable(f"{date}: 직전 거래일 데이터가 없어 분기보정 수익률을 계산할 수 없습니다")

    computed = compute(nav, pd.Series([None] * len(nav)))
    row = computed[computed["date"] == date]
    if row.empty:
        raise NavDataUnavailable(f"{date}: NAV v2 계산 결과가 없습니다")
    row = row.iloc[0]

    if pd.isna(row["divergence"]) or pd.isna(row["nav_implied_ret"]):
        raise NavDataUnavailable(
            f"{date}: NAV v2 계산 결과가 유효하지 않습니다(divergence/nav_implied_ret 결측)"
        )

    def _f(v):
        return None if pd.isna(v) else float(v)

    return {
        "date": date,
        "skq_ret": _f(row["skq_ret_v2"]),
        "hynix_ret": _hynix_ret(date),
        "nav_total_trillion": _f(row["nav_total_trillion"]),
        "nav_implied_ret": _f(row["nav_implied_ret"]),
        "nav_implied_ret_raw": _f(row["nav_implied_ret_raw"]),
        "divergence": _f(row["divergence"]),
        "nav_discount_pct": _f(row["nav_discount_pct"]),
        "nav_discount_delta": _f(row["nav_discount_delta"]),
        "nav_per_share": _f(row["nav_per_share"]),
        "skq_shares_v2": None if pd.isna(row["skq_shares_v2"]) else int(row["skq_shares_v2"]),
    }
