"""
Futures Backfill — data/futures_log.csv 생성/갱신

factor_log.csv의 각 거래일에 대해 SK스퀘어 주식선물(KRDRVFUEQU)을 pykrx로 조회해
별도 파일 data/futures_log.csv에 누적한다.

설계 의도
  - factor_log.csv(코어 데이터)를 건드리지 않고 분리 — 선물은 KRX 상장이 간헐적이라
    listed=0 구간이 많고(2021~2022·2025 미상장, 2023~2024·2026 상장), 별도 cadence가 자연스럽다.
  - 모델링 시 features.py에서 date 기준 left-join으로 합친다.
  - 재개 가능(이미 기록된 날짜 skip), throttle.
  - OI(미결제약정)는 pykrx 미지원이라 수집 안 함 (basis/volume만).

사용
  python main.py backfill-futures
"""
import os
import csv
import time
from pathlib import Path

from loguru import logger

# tools.futures 임포트 시점에 사내망 SSL 프록시 우회가 적용됨(기존 공통 패턴).
# 별도 SSL 처리를 여기서 중복하지 않는다.
from tools.futures import get_futures_data
from tools.factor_logger import LOG_PATH

FUT_LOG = Path(LOG_PATH).parent / "futures_log.csv"
FUT_COLS = ["date", "fut_listed", "fut_basis", "fut_basis_pct", "fut_volume"]


def _factor_dates() -> list:
    """factor_log.csv의 거래일 리스트 (백필 기준 날짜)."""
    if not Path(LOG_PATH).exists():
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        return [r["date"] for r in csv.DictReader(f) if r.get("date")]


def _existing_dates() -> set:
    if not FUT_LOG.exists():
        return set()
    with open(FUT_LOG, encoding="utf-8") as f:
        return {r["date"] for r in csv.DictReader(f) if r.get("date")}


def backfill_futures(throttle: float = 0.3) -> dict:
    """
    Returns: {"ok": int, "listed": int, "fail": int, "total": int}
    """
    ticker = os.getenv("COMPANY_TICKER", "402340")
    dates = _factor_dates()
    if not dates:
        logger.error("factor_log.csv 거래일을 못 읽음. 백필 중단.")
        return {"ok": 0, "listed": 0, "fail": 0, "total": 0}

    existing = _existing_dates()
    todo = [d for d in dates if d not in existing]
    logger.info(f"선물 백필 대상 {len(todo)}일 (기존 {len(existing)}일 skip, factor_log {len(dates)}일)")

    new_file = not FUT_LOG.exists()
    ok = listed = fail = 0
    with open(FUT_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FUT_COLS)
        if new_file:
            writer.writeheader()

        for i, d in enumerate(todo, 1):
            try:
                fut = get_futures_data(ticker, d)
                is_listed = bool(fut.get("listed"))
                nm = fut.get("near_month", {}) if is_listed else {}
                writer.writerow({
                    "date":          d,
                    "fut_listed":    1 if is_listed else 0,
                    "fut_basis":     fut.get("basis") if is_listed else "",
                    "fut_basis_pct": fut.get("basis_pct") if is_listed else "",
                    "fut_volume":    nm.get("volume") if is_listed else "",
                })
                ok += 1
                listed += 1 if is_listed else 0
            except Exception as e:
                fail += 1
                writer.writerow({"date": d, "fut_listed": "", "fut_basis": "",
                                 "fut_basis_pct": "", "fut_volume": ""})
                logger.warning(f"[{d}] 선물 수집 실패: {e}")

            if i % 50 == 0 or i == len(todo):
                f.flush()
                logger.info(f"진행 {i}/{len(todo)} | 상장 {listed}일 | 실패 {fail}")
            if throttle:
                time.sleep(throttle)

    logger.info(f"선물 백필 완료 — 기록 {ok}일, 상장 {listed}일, 실패 {fail}, 파일 {FUT_LOG}")
    return {"ok": ok, "listed": listed, "fail": fail, "total": len(existing) + ok}
