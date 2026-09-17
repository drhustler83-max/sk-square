"""
SK스퀘어 상장사 보유지분 일별 가치 산출 → data/listed_holdings_daily.csv

보유주식수 이력의 근거
  · 뼈대  DART 「타법인 출자현황」(otrCprInvstmntSttus) — 반기·사업보고서에만 실리므로
          연 2회 스냅샷. 분기말 잔량의 체크섬 역할을 한다.
  · 일자  분기 중 변동일은 정기공시에 없으므로 수시공시·대량보유상황보고(majorstock)·
          언론 보도로 확정했다. 확정하지 못한 건은 date_confidence 로 표시한다.

확정 내역
  SK하이닉스   146,100,000주 전 기간 불변 (최초취득 2021-11-02)
  드림어스      29,246,387 → 16,430,038  @2025-11-28  (majorstock, 주식매매계약 체결·특별관계 해소)
  인크로스      2,786,455 → 4,631,251    @무상증자 권리락일 (1주당 0.66주, 2022 H1)
                4,631,251 → 0            @2026-01-02  (majorstock, SK네트웍스 인수 완료)
  나노엔텍      7,600,649 → 0            @2023-09-08  (매각대금 입금 완료, 언론 확정)
  크래프톤      0 → 1,085,600            @2022-12-29  (SK플래닛 펀드 현물배당, 최초취득일)
                1,085,600 → 0            @2024-04-23  (2024-04-22 장마감 후 블록딜)
  IONQ         0 → 2,797,720             @2025-04-30  (IDQ 지분교환, 최초취득일)
                2,797,720 → 1,307,720    @2026-06-30  ★가정 — 국내 공시 의무 없어 일자 미확인
  넥써쓰        0 → 12,111,300           @2026-06-26  (제3자배정 유상증자, 최초취득일)

평가 규칙
  · 국내 6종목: KRX 종가 × 보유주식수
  · IONQ: 나스닥 종가 × USD/KRW. 한국 장마감(15:30 KST) 시점에 알 수 있는 가장 최근
    미국 종가는 전 영업일 것이므로 1거래일 시프트한다 (look-ahead 방지).
  · 무상증자는 주식수와 주가가 같은 날 조정되므로 권리락일만 맞으면 가치가 연속이다.
    권리락일은 주가 급락폭으로 자동 탐지하고 예상 비율과 대조한다.

Usage:
  python tools/build_listed_holdings.py
"""

from __future__ import annotations

import argparse
import ssl
import sys
from pathlib import Path

import urllib3
import requests

# 사내망 SSL 프록시 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

import pandas as pd
from pykrx import stock
from loguru import logger

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

START = "20211130"
END = "20260916"
DEFAULT_OUTPUT = BASE / "data" / "listed_holdings_daily.csv"

SKQ = "402340"

# (키, 표시명, 종목코드)
KR_STOCKS = [
    ("skhynix",   "SK하이닉스",    "000660"),
    ("dreamus",   "드림어스컴퍼니", "060570"),
    ("incross",   "인크로스",      "216050"),
    ("nanoentek", "나노엔텍",      "039860"),
    ("krafton",   "크래프톤",      "259960"),
    ("nexus",     "넥써쓰",        "205500"),
]

# 보유주식수 구간 — (시작일 포함, 종료일 포함, 주식수, 근거, 신뢰도)
#   종료일 None = 마지막까지
HOLDINGS: dict[str, list[tuple[str, str | None, int, str, str]]] = {
    "skhynix": [
        ("20211130", None, 146_100_000, "타법인출자현황 전 기간 불변", "confirmed"),
    ],
    "dreamus": [
        ("20211130", "20251127", 29_246_387, "타법인출자현황", "confirmed"),
        ("20251128", None,       16_430_038, "majorstock 2025-11-28 특별관계 해소", "confirmed"),
    ],
    "incross": [
        # 2022 H1 무상증자(1주당 0.66주)로 2,786,455 → 4,631,251 이 되었으나,
        # pykrx 는 수정주가를 반환하므로(과거 주가가 이미 1.662 로 나눠져 있음)
        # 전 기간에 무상증자 '후' 주식수를 적용해야 가치가 연속·정확하다.
        #   검증: 2022년 인크로스 일간 최대 하락이 -10.8% 로, 권리락 -40% 가 없음
        ("20211130", "20251231", 4_631_251, "무상증자 후 기준(수정주가 정합)", "confirmed"),
        ("20260102", None,       0,         "majorstock 2026-01-02 SK네트웍스 인수완료", "confirmed"),
    ],
    "nanoentek": [
        ("20211130", "20230907", 7_600_649, "타법인출자현황", "confirmed"),
        ("20230908", None,       0,         "2023-09-08 매각대금 입금 완료", "confirmed"),
    ],
    "krafton": [
        ("20221229", "20240422", 1_085_600, "2022-12-29 현물배당 취득", "confirmed"),
        ("20240423", None,       0,         "2024-04-22 장마감후 블록딜", "confirmed"),
    ],
    "nexus": [
        ("20260626", None, 12_111_300, "2026-06-26 제3자배정 신주취득", "confirmed"),
    ],
    "ionq": [
        ("20250430", "20260203", 2_797_720, "2025-04-30 IDQ 지분교환 취득", "confirmed"),
        ("20260204", None,       1_307_720, "2026-02-04 -1,490,000 (Sean 확인)", "confirmed"),
    ],
}

# 인크로스 무상증자: 1주당 0.66주 → 목표 주식수
INCROSS_BONUS_TARGET = 4_631_251
INCROSS_BONUS_RATIO = INCROSS_BONUS_TARGET / 2_786_455   # ≈ 1.662


def _kr_prices(ticker: str, name: str) -> pd.Series:
    df = stock.get_market_ohlcv(START, END, ticker)
    if df is None or df.empty:
        logger.warning(f"{name}({ticker}) 시세 없음")
        return pd.Series(dtype=float)
    s = df["종가"].astype(float)
    s.index = pd.to_datetime(s.index)
    logger.info(f"{name}({ticker}) {len(s)}일  {s.index.min().date()} ~ {s.index.max().date()}")
    return s


def _verify_adjusted(px: pd.Series) -> None:
    """pykrx 가 수정주가를 주는지 검증 — 인크로스 2022년에 권리락 급락이 없어야 한다.

    급락이 발견되면 원주가라는 뜻이므로, incross 주식수를 무상증자 '전/후' 로
    분할해야 한다. 그 경우 경고를 남긴다.
    """
    win = px.loc["2022-01-01":"2022-12-31"]
    if len(win) < 5:
        return
    worst = float((win / win.shift(1)).min())
    expected_exrights = 1.0 / INCROSS_BONUS_RATIO      # ≈ 0.602
    if worst < expected_exrights + 0.12:
        logger.error(
            f"인크로스 2022년 최대 하락 {worst:.4f} — 권리락({expected_exrights:.3f}) 로 보임. "
            f"pykrx 가 원주가를 반환하고 있으므로 incross 주식수 구간을 분할해야 함!"
        )
    else:
        logger.info(f"수정주가 확인: 인크로스 2022년 최대 하락 {worst:.4f} "
                    f"(권리락 {expected_exrights:.3f} 없음) → 무상증자 후 주식수 적용 타당")


def _shares_series(key: str, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """구간 정의 → 일별 주식수 + 신뢰도 시리즈."""
    sh = pd.Series(0, index=index, dtype="int64")
    conf = pd.Series("none", index=index, dtype=object)
    for start, end, qty, _why, c in HOLDINGS[key]:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end) if end else index.max()
        mask = (index >= s) & (index <= e)
        sh.loc[mask] = qty
        conf.loc[mask] = c
    return sh, conf


def build(output: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    # ── 기준 거래일: SK스퀘어 거래일 ──
    skq = stock.get_market_ohlcv(START, END, SKQ)
    idx = pd.to_datetime(skq.index)
    idx = idx[(idx >= pd.Timestamp(START)) & (idx <= pd.Timestamp(END))]
    logger.info(f"기준 거래일 {len(idx)}일  {idx.min().date()} ~ {idx.max().date()}")

    out = pd.DataFrame(index=idx)
    out.index.name = "date"

    # ── 국내 6종목 ──
    prices: dict[str, pd.Series] = {}
    for key, name, tk in KR_STOCKS:
        prices[key] = _kr_prices(tk, name)

    # 수정주가 전제 검증 (인크로스 무상증자)
    _verify_adjusted(prices["incross"])

    total = pd.Series(0.0, index=idx)
    for key, name, _tk in KR_STOCKS:
        px = prices[key].reindex(idx).ffill()
        sh, conf = _shares_series(key, idx)
        val = px.fillna(0) * sh
        out[f"{key}_shares"] = sh
        out[f"{key}_price"] = px
        out[f"{key}_value"] = val
        out[f"{key}_conf"] = conf
        total += val.fillna(0)

    # ── IONQ (나스닥) ──
    import FinanceDataReader as fdr
    ionq = fdr.DataReader("IONQ", START, END)["Close"].astype(float)
    ionq.index = pd.to_datetime(ionq.index)
    # 한국 장마감 시점에 알 수 있는 최신 미국 종가 = 전 영업일 → 1일 시프트
    ionq_kr = ionq.reindex(idx.union(ionq.index)).ffill().shift(1).reindex(idx)
    logger.info(f"IONQ {len(ionq)}일 (미국) → 한국 거래일 매핑, 1일 시프트 적용")

    fx = fdr.DataReader("USD/KRW", START, END)["Close"].astype(float)
    fx.index = pd.to_datetime(fx.index)
    fx_kr = fx.reindex(idx.union(fx.index)).ffill().reindex(idx)

    sh, conf = _shares_series("ionq", idx)
    ionq_val = ionq_kr.fillna(0) * fx_kr.fillna(0) * sh
    out["ionq_shares"] = sh
    out["ionq_price_usd"] = ionq_kr
    out["usdkrw"] = fx_kr
    out["ionq_value"] = ionq_val
    out["ionq_conf"] = conf
    total += ionq_val.fillna(0)

    out["total_listed_value"] = total
    out["total_listed_value_trillion"] = (total / 1e12).round(4)

    out = out.reset_index()
    out["date"] = out["date"].dt.strftime("%Y%m%d")
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False, encoding="utf-8-sig")
    logger.info(f"저장: {output}  ({len(out)}행)")
    return out


def _summary(df: pd.DataFrame) -> None:
    print(f"\n{'='*104}")
    print(f"  SK스퀘어 상장사 보유지분 일별 가치 — {len(df)}거래일 "
          f"({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")
    print(f"{'='*104}")
    keys = [k for k, _, _ in KR_STOCKS] + ["ionq"]
    print(f"\n  {'종목':12} {'보유 시작':>10} {'보유 종료':>10} {'주식수 변화':>34}")
    for k in keys:
        sh = df[f"{k}_shares"]
        held = df.loc[sh > 0, "date"]
        if held.empty:
            print(f"  {k:12} {'-':>10} {'-':>10} {'(보유 없음)':>34}")
            continue
        uniq = sh[sh > 0].unique()
        chg = " → ".join(f"{int(v):,}" for v in uniq)
        print(f"  {k:12} {held.iloc[0]:>10} {held.iloc[-1]:>10} {chg:>34}")

    print(f"\n  ── 분기말 상장사 가치 합계 (조원) ──")
    d = df.copy()
    d["q"] = d["date"].str[:4] + "Q" + ((d["date"].str[4:6].astype(int) - 1) // 3 + 1).astype(str)
    for q, g in d.groupby("q"):
        last = g.iloc[-1]
        print(f"    {q}  {last['date']}  {last['total_listed_value_trillion']:>8.4f}조")

    n_assumed = int((df["ionq_conf"] == "ASSUMED").sum())
    if n_assumed:
        print(f"\n  ★ IONQ 처분일 미확인 구간 {n_assumed}일 (ionq_conf=ASSUMED) — 확인 후 갱신 필요")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()
    df = build(args.output)
    _summary(df)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
