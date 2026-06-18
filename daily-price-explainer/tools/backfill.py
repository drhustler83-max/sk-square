"""
Factor Log Backfill
SK스퀘어 상장 이후 전 구간에 대해 collect_and_log를 순회 실행하여
data/factor_log.csv를 한 번에 채운다.

설계:
  - 라이브 로직 재사용: collect_and_log(date)를 그대로 호출 → 백필 데이터와
    매일 누적 데이터가 동일한 계산 경로를 통과 (정합성 보장)
  - 거래일 리스트: pykrx SKQ OHLCV 인덱스에서 추출 (휴장일 자동 제외)
  - 재개 가능: 이미 CSV에 있는 날짜는 건너뜀 (중간에 끊겨도 재실행하면 이어감)
  - throttle: KRX 차단 방지용 호출 간 대기 (기본 0.25초)
  - diskcache가 tool 결과를 캐싱 → 재실행은 캐시 히트로 빠름

사용:
  python main.py backfill                 # 상장일 ~ 오늘 (기존 행 유지)
  python main.py backfill 20240101        # 시작일 지정 ~ 오늘
  python main.py backfill 20240101 20241231
  python main.py backfill --rebuild       # CSV 초기화 후 전체 재생성 (권장: 1회)
"""
import os
import csv
import time
import ssl
import urllib3
import requests

# 사내망 SSL 프록시 우회 (pykrx)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context
_orig = requests.Session.request
def _no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig(self, *args, **kwargs)
requests.Session.request = _no_verify

from datetime import datetime
from pykrx import stock
from loguru import logger

from tools.factor_logger import collect_and_log, LOG_PATH, date_count

# SK스퀘어 분할 재상장일(2021-11-29) 이전으로 시작점을 잡아도 pykrx가
# 실제 첫 상장일부터 클리핑하므로 안전한 여유 시작값을 사용.
DEFAULT_START = "20211101"


def _trading_days(start: str, end: str, ticker: str) -> list:
    """SKQ OHLCV 인덱스에서 실제 거래일(YYYYMMDD) 리스트 추출."""
    df = stock.get_market_ohlcv(start, end, ticker)
    if df is None or df.empty:
        return []
    return [d.strftime("%Y%m%d") for d in df.index]


def _existing_dates() -> set:
    """이미 CSV에 기록된 날짜 집합 (재개용)."""
    if not LOG_PATH.exists():
        return set()
    with open(LOG_PATH, encoding="utf-8") as f:
        return {r["date"] for r in csv.DictReader(f) if r.get("date")}


def backfill(start: str = DEFAULT_START, end: str = None,
             throttle: float = 0.25, rebuild: bool = False) -> dict:
    """
    Returns: {"ok": int, "fail": int, "total_rows": int, "failed_dates": list}
    """
    ticker = os.getenv("COMPANY_TICKER", "402340")
    end = end or datetime.today().strftime("%Y%m%d")

    if rebuild and LOG_PATH.exists():
        LOG_PATH.unlink()
        logger.info(f"기존 로그 삭제(rebuild): {LOG_PATH}")

    days = _trading_days(start, end, ticker)
    if not days:
        logger.error("거래일 리스트 조회 실패 (네트워크/로그인/티커 확인). 백필 중단.")
        return {"ok": 0, "fail": 0, "total_rows": date_count(), "failed_dates": []}

    logger.info(f"거래일 {len(days)}일 ({days[0]} ~ {days[-1]})")
    existing = _existing_dates()
    todo = [d for d in days if d not in existing]
    logger.info(f"백필 대상 {len(todo)}일 (기존 {len(existing)}일 skip)")

    ok = 0
    fail = 0
    failed_dates = []
    for i, d in enumerate(todo, 1):
        try:
            collect_and_log(d)
            ok += 1
        except Exception as e:
            fail += 1
            failed_dates.append(d)
            logger.warning(f"[{d}] 수집 실패: {e}")
        if i % 20 == 0 or i == len(todo):
            logger.info(f"진행 {i}/{len(todo)} | 성공 {ok} 실패 {fail} | 누적 {date_count()}행")
        if throttle:
            time.sleep(throttle)

    logger.info(f"백필 완료 — 성공 {ok}, 실패 {fail}, 총 {date_count()}행")
    if failed_dates:
        logger.warning(f"실패한 날짜({len(failed_dates)}): {failed_dates}")
    return {"ok": ok, "fail": fail, "total_rows": date_count(), "failed_dates": failed_dates}
