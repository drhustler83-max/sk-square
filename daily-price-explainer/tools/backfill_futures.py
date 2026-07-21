"""
Futures Backfill — data/factor_log.csv의 fut_listed/fut_basis/fut_basis_pct/fut_volume 채움

2026-07-21: 별도 파일(futures_log.csv)을 factor_log.csv에 병합 후 폐기 — 이 스크립트도
이제 factor_log.csv를 직접 읽고 갱신한다 (별도 cadence 파일 없음).

설계
  - factor_log.csv를 읽어 fut_listed가 비어있는(미백필/실패) 행만 대상으로 조회.
  - 재개 가능: 이미 값(0 또는 1)이 채워진 행은 skip. 조회 실패 행은 빈 값으로 남아
    다음 실행에서 자동 재시도된다.
  - throttle: KRX 차단 방지용 호출 간 대기.
  - OI(미결제약정)는 pykrx 미지원이라 수집 안 함 (basis/volume만).

사용
  python main.py backfill-futures
"""
import os
import time
from pathlib import Path

import pandas as pd
from loguru import logger

# tools.futures 임포트 시점에 사내망 SSL 프록시 우회가 적용됨(기존 공통 패턴).
from tools.futures import get_futures_data
from tools.factor_logger import LOG_PATH

FUT_COLS = ["fut_listed", "fut_basis", "fut_basis_pct", "fut_volume"]


def backfill_futures(throttle: float = 0.3) -> dict:
    """
    Returns: {"ok": int, "listed": int, "fail": int, "total": int}
    """
    ticker = os.getenv("COMPANY_TICKER", "402340")
    if not Path(LOG_PATH).exists():
        logger.error("factor_log.csv 없음. 백필 중단.")
        return {"ok": 0, "listed": 0, "fail": 0, "total": 0}

    df = pd.read_csv(LOG_PATH, dtype=str)
    for c in FUT_COLS:
        if c not in df.columns:
            df[c] = ""

    is_blank = df["fut_listed"].isna() | (df["fut_listed"].fillna("").str.strip() == "")
    todo_idx = df.index[is_blank].tolist()
    logger.info(f"선물 백필 대상 {len(todo_idx)}일 (factor_log {len(df)}일)")

    ok = listed = fail = 0
    for n, i in enumerate(todo_idx, 1):
        d = df.at[i, "date"]
        try:
            fut = get_futures_data(ticker, d)
            is_listed = bool(fut.get("listed"))
            nm = fut.get("near_month", {}) if is_listed else {}
            df.at[i, "fut_listed"]    = "1" if is_listed else "0"
            df.at[i, "fut_basis"]     = str(fut.get("basis")) if is_listed else ""
            df.at[i, "fut_basis_pct"] = str(fut.get("basis_pct")) if is_listed else ""
            df.at[i, "fut_volume"]    = str(nm.get("volume")) if is_listed else ""
            ok += 1
            listed += 1 if is_listed else 0
        except Exception as e:
            fail += 1
            logger.warning(f"[{d}] 선물 수집 실패: {e}")

        if n % 50 == 0 or n == len(todo_idx):
            df.to_csv(LOG_PATH, index=False, na_rep="")
            logger.info(f"진행 {n}/{len(todo_idx)} | 상장 {listed}일 | 실패 {fail}")
        if throttle:
            time.sleep(throttle)

    df.to_csv(LOG_PATH, index=False, na_rep="")
    logger.info(f"선물 백필 완료 — 기록 {ok}일, 상장 {listed}일, 실패 {fail}")
    return {"ok": ok, "listed": listed, "fail": fail, "total": len(df)}
