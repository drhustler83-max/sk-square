"""
Factor Logger
매 영업일 장 마감 후 팩터 데이터를 data/factor_log.csv에 한 행씩 누적.

저장 컬럼:
  date                    — YYYYMMDD
  skq_ret                 — SK스퀘어 일간 수익률 (%)
  hynix_ret               — SK하이닉스 일간 수익률 (%)
  nav_total_trillion      — 총 NAV (조원)
  nav_implied_ret         — NAV 증가율 (%, 할인율 고정 가정)
  divergence              — 잔차 = skq_ret - nav_implied_ret (%p)
  nav_discount_pct        — NAV 할인율 (%, 양수=할인)
  nav_discount_delta      — NAV 할인율 전일 대비 변화 (%p, 음수=축소)
  sector_semiconductor    — 반도체 섹터 ETF 평균 수익률 (%)
  kospi_ret               — KOSPI 일간 수익률 (%)
  usd_krw                 — USD/KRW 환율 (당일)
  foreign_net             — 외국인 순매수 (원, SK스퀘어 종목)
  institution_net         — 기관 순매수 (원, SK스퀘어 종목)

60거래일 누적 후 tools/beta.py에서 rolling OLS로 팩터 베타 산출 예정.
"""
import csv
import os
from datetime import datetime
from pathlib import Path
from loguru import logger

_BASE = Path(__file__).parent.parent
LOG_PATH = _BASE / "data" / "factor_log.csv"

COLUMNS = [
    "date",
    "skq_ret",
    "hynix_ret",
    "nav_total_trillion",
    "nav_implied_ret",
    "divergence",
    "nav_discount_pct",
    "nav_discount_delta",
    "sector_semiconductor",
    "kospi_ret",
    "usd_krw",
    "foreign_net",
    "institution_net",
    "shorting_balance",       # 공매도 잔고수량 (주)
    "shorting_balance_ratio", # 공매도 잔고율 (%)
    "shorting_volume_ratio",  # 당일 공매도 비중 (%)
    "shorting_balance_change",# 잔고 전일 대비 변화 (주)
]


def collect_and_log(date: str = None) -> dict:
    """
    장 마감 후 팩터 데이터 수집 → CSV 저장.

    Args:
        date: 'YYYYMMDD'. None이면 오늘 날짜.
    Returns:
        저장된 row dict
    """
    if date is None:
        date = datetime.today().strftime("%Y%m%d")

    ticker = os.getenv("COMPANY_TICKER", "402340")
    row: dict = {"date": date}

    # ── 1. NAV (SK스퀘어·하이닉스 수익률, divergence, 할인율 포함) ─────────
    try:
        from tools.nav import get_nav_data
        nav = get_nav_data(date)

        row["skq_ret"]            = nav.get("skq_pct")
        row["nav_total_trillion"] = nav.get("nav_total_trillion")
        row["nav_implied_ret"]    = nav.get("nav_implied_pct")
        row["divergence"]         = nav.get("divergence")
        row["nav_discount_pct"]   = nav.get("nav_discount_pct")
        row["nav_discount_delta"] = nav.get("nav_discount_delta")

        for h in nav.get("hynix_nav_change", []):
            if "하이닉스" in h.get("name", ""):
                row["hynix_ret"] = h.get("pct_change")
                break

        logger.info(
            f"[{date}] SKQ {row.get('skq_ret'):+.2f}% | "
            f"HYX {row.get('hynix_ret'):+.2f}% | "
            f"divergence {row.get('divergence'):+.2f}%p | "
            f"discount {row.get('nav_discount_pct'):+.1f}%"
        )
    except Exception as e:
        logger.warning(f"NAV 수집 오류: {e}")

    # ── 2. 수급 (SK스퀘어 외국인·기관 순매수) ────────────────────────────
    try:
        from tools.market import get_market_data
        market = get_market_data(ticker, date)
        flow = market.get("investor_flow", {})
        row["foreign_net"]     = flow.get("foreign_net")
        row["institution_net"] = flow.get("institution_net")
    except Exception as e:
        logger.warning(f"Market 수집 오류: {e}")

    # ── 3. 매크로 (KOSPI 수익률, USD/KRW) ───────────────────────────────
    try:
        from tools.macro import get_macro_data
        macro = get_macro_data(date)
        row["kospi_ret"] = macro.get("kospi", {}).get("pct_change")
        row["usd_krw"]   = macro.get("usd_krw")
    except Exception as e:
        logger.warning(f"Macro 수집 오류: {e}")

    # ── 4. 반도체 섹터 수익률 ────────────────────────────────────────────
    try:
        from tools.sector_rotation import get_sector_rotation
        rotation = get_sector_rotation(date)
        for sector, pct in rotation.get("rotation_signal", {}).get("all_sectors_ranked", []):
            if sector == "반도체":
                row["sector_semiconductor"] = pct
                break
    except Exception as e:
        logger.warning(f"Sector 수집 오류: {e}")

    # ── 5. 공매도 데이터 ─────────────────────────────────────────────────
    try:
        from tools.short import get_shorting_data
        sh = get_shorting_data(ticker, date)
        row["shorting_balance"]        = sh.get("shorting_balance")
        row["shorting_balance_ratio"]  = sh.get("shorting_balance_ratio")
        row["shorting_volume_ratio"]   = sh.get("shorting_volume_ratio")
        row["shorting_balance_change"] = sh.get("balance_change")
    except Exception as e:
        logger.warning(f"공매도 수집 오류: {e}")

    # ── 6. CSV 저장 ──────────────────────────────────────────────────────
    _append_to_csv(row)
    return row


def _append_to_csv(row: dict) -> None:
    """row를 CSV에 추가. 파일 없으면 헤더 포함 생성.
    컬럼이 추가된 경우 기존 CSV를 마이그레이션(누락 컬럼을 빈값으로 채움).
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if LOG_PATH.exists():
        # 기존 헤더 확인 → 컬럼 추가 시 마이그레이션
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            existing_cols = f.readline().strip().split(",")
        new_cols = [c for c in COLUMNS if c not in existing_cols]
        if new_cols:
            logger.info(f"CSV 컬럼 마이그레이션: {new_cols} 추가")
            _migrate_csv(existing_cols)

        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writerow({col: row.get(col, "") for col in COLUMNS})
    else:
        with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerow({col: row.get(col, "") for col in COLUMNS})

    logger.info(f"팩터 로그 저장: {LOG_PATH} ({date_count()} 행)")


def _migrate_csv(old_cols: list) -> None:
    """기존 CSV를 읽어 누락 컬럼을 빈값으로 채운 후 덮어씀."""
    rows = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({col: r.get(col, "") for col in COLUMNS})


def date_count() -> int:
    """현재 누적 영업일 수"""
    if not LOG_PATH.exists():
        return 0
    with open(LOG_PATH, encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)  # 헤더 제외


def load_log() -> "pd.DataFrame":
    """누적 로그를 DataFrame으로 반환 (beta.py에서 사용)"""
    import pandas as pd
    if not LOG_PATH.exists():
        raise FileNotFoundError(f"로그 없음: {LOG_PATH}")
    df = pd.read_csv(LOG_PATH, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # usd_krw 변화율은 CSV에서 직접 계산 (로거 실행 시 불필요한 이중 API 호출 방지)
    df["usd_krw_chg_pct"] = df["usd_krw"].pct_change() * 100
    return df


def get_discount_stats(windows: list = None) -> dict:
    """
    NAV 할인율 롤링 통계 반환.

    Returns:
        {
          "n_days": 누적 영업일 수,
          "current": 가장 최근 할인율 (%),
          "stats": {
              20: {"mean": float, "min": float, "max": float},  # 데이터 충분 시
              60: {"mean": float, "min": float, "max": float},
          },
          "trend": "축소" | "확대" | "보합" | None,   # 최근 5일 선형 방향
        }
        데이터 부족 시 "n_days" 만 반환.
    """
    import pandas as pd
    import numpy as np

    if windows is None:
        windows = [20, 60]

    if not LOG_PATH.exists():
        return {"n_days": 0}

    try:
        df = pd.read_csv(LOG_PATH)
        df = df.dropna(subset=["nav_discount_pct"]).reset_index(drop=True)
        n = len(df)
        if n == 0:
            return {"n_days": 0}

        current = float(df["nav_discount_pct"].iloc[-1])
        result: dict = {"n_days": n, "current": current, "stats": {}}

        for w in windows:
            if n >= w:
                window_data = df["nav_discount_pct"].iloc[-w:]
                result["stats"][w] = {
                    "mean": round(float(window_data.mean()), 1),
                    "min":  round(float(window_data.min()), 1),
                    "max":  round(float(window_data.max()), 1),
                }

        # 최근 5일 선형 추세 (slope 부호)
        if n >= 5:
            recent = df["nav_discount_pct"].iloc[-5:].values.astype(float)
            x = np.arange(len(recent))
            slope = float(np.polyfit(x, recent, 1)[0])
            if abs(slope) < 0.05:
                result["trend"] = "보합"
            elif slope < 0:
                result["trend"] = "축소"   # 할인율 감소 = 축소
            else:
                result["trend"] = "확대"
        else:
            result["trend"] = None

        return result

    except Exception as e:
        logger.warning(f"get_discount_stats 오류: {e}")
        return {"n_days": date_count()}
