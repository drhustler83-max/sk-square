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
import tempfile
from datetime import datetime
from pathlib import Path
from loguru import logger

_BASE = Path(__file__).parent.parent
LOG_PATH = _BASE / "data" / "factor_log.csv"

# COLUMNS는 "이 로거가 매일 채우는 항목"의 기준 목록이자 신규 파일 생성 시의
# 컬럼 순서다. CSV에 이미 있는 컬럼(예: NAV v2 마이그레이션의 *_v1, skq_shares_v2
# 등, 2026-09 사고 이후 재발 방지)은 이 목록에 없어도 저장 시 그대로 보존된다.
# 실제 쓰기 컬럼 계산은 _fieldnames() 참고.
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
    "fut_listed",             # 주식선물 상장 여부 0/1 (futures_log.csv 병합, 2026-07-21)
    "fut_basis",              # 근월물 베이시스 (선물-현물, 원)
    "fut_basis_pct",          # 근월물 베이시스 (%)
    "fut_volume",             # 근월물 거래량
    "foreign_own_pct",        # 외국인 지분율 (%, extra_log.csv 병합, 2026-07-21)
    "individual_net",         # 개인 순매수 (거래대금, 원)
    # ── NAV v2 진단용 보조 컬럼 (tools/nav_v2.compute가 매번 함께 냄) ───────
    "nav_implied_ret_raw",  # 분기 계단 제거 전 원시 차분 (참고용)
    "nav_per_share",        # 주당 NAV
    "skq_shares_v2",        # 그날 반영된 발행주식총수 (자사주 소각 반영)
    # ── 2026-09-17 NAV v1→v2 마이그레이션 당시 원본 보존 컬럼 ───────────────
    #   신규 날짜에는 채워지지 않는다(그 날짜엔 "v1"이라는 옛 계산이 애초에
    #   없으므로 빈 값이 맞다). 여기 적어 두는 건 신규 CSV 생성 시 순서를
    #   현재 파일과 맞추기 위함이고, 실제 보존은 _fieldnames()/_merge_row()가
    #   기존 헤더를 우선하므로 이 목록에서 빠지더라도 사라지지 않는다.
    "nav_total_trillion_v1",
    "nav_implied_ret_v1",
    "divergence_v1",
    "nav_discount_pct_v1",
    "nav_discount_delta_v1",
    "skq_ret_v1",
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
    # tools.nav_v2.get_live_nav_v2()가 point-in-time 파이프라인(data/nav_daily.csv,
    # tools/build_nav_daily.py, tools/apply_nav_v2.py)과 같은 계산 함수를 쓴다
    # (구 company_context.py 고정 스냅샷 방식은 더 이상 쓰지 않는다).
    #
    # ⚠ 여기서 예외가 나면(자료 부족·당일 시세 미확정 등) 이 날짜는 통째로
    # 저장하지 않는다 — NAV가 없는데 다른 컬럼만 채워 저장하면 "결측"과
    # "수집 안 함"이 구분 안 되고, v1 방식으로 대충 채우면 2026-09-22 사고와
    # 다른 형태로 같은 문제(신뢰 안 되는 값이 조용히 섞여 들어감)가 재발한다.
    try:
        from tools.nav_v2 import get_live_nav_v2
        nav = get_live_nav_v2(date)
    except Exception as e:
        logger.error(f"[{date}] NAV v2 수집 실패 — 이 날짜 저장을 보류합니다: {e}")
        raise  # tools/backfill.py 등 호출부가 실패로 집계하도록 예외를 그대로 전파

    row["skq_ret"]            = nav.get("skq_ret")
    row["hynix_ret"]          = nav.get("hynix_ret")
    row["nav_total_trillion"] = nav.get("nav_total_trillion")
    row["nav_implied_ret"]    = nav.get("nav_implied_ret")
    row["divergence"]         = nav.get("divergence")
    row["nav_discount_pct"]   = nav.get("nav_discount_pct")
    row["nav_discount_delta"] = nav.get("nav_discount_delta")
    row["nav_implied_ret_raw"] = nav.get("nav_implied_ret_raw")
    row["nav_per_share"]       = nav.get("nav_per_share")
    row["skq_shares_v2"]       = nav.get("skq_shares_v2")

    def _fmt(v):
        return float("nan") if v is None else v

    logger.info(
        f"[{date}] SKQ {_fmt(row.get('skq_ret')):+.2f}% | "
        f"HYX {_fmt(row.get('hynix_ret')):+.2f}% | "
        f"divergence {_fmt(row.get('divergence')):+.2f}%p | "
        f"discount {_fmt(row.get('nav_discount_pct')):+.1f}%"
    )

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


def _fieldnames(existing_header: list, row: dict) -> list:
    """저장에 쓸 전체 컬럼 목록.

    기존 CSV 헤더 순서를 그대로 보존하고, 거기 없는 컬럼(COLUMNS 기준 항목이든
    이번 수집에서 처음 등장한 키든)만 끝에 덧붙인다. 컬럼을 목록에서 빠뜨려도
    조용히 지워지지 않는다 — 늘어나기만 한다.
    """
    fieldnames = list(existing_header)
    for col in list(COLUMNS) + list(row.keys()):
        if col not in fieldnames:
            fieldnames.append(col)
    return fieldnames


def _merge_row(existing: dict, incoming: dict, fieldnames: list) -> dict:
    """기존 행에 이번 수집분만 덮어써 병합한다.

    incoming에 없는 컬럼(예: NAV v2 마이그레이션이 붙인 *_v1, skq_shares_v2 등 —
    이 로거가 아예 모르는 컬럼)은 기존 값을 그대로 유지한다. incoming 값이
    None이면 "이번에 수집 못 함"으로 보고 역시 기존 값을 유지한다. 0은 정상
    수집값이므로 덮어쓴다 — None 여부만으로 판단하고 값의 참/거짓은 보지 않는다.
    """
    merged = {col: (existing or {}).get(col, "") for col in fieldnames}
    for col, val in incoming.items():
        if val is not None:
            merged[col] = val
    return merged


def _atomic_write_csv(fieldnames: list, rows: list) -> None:
    """임시 파일에 전부 쓴 뒤 원본을 교체한다 — 쓰는 도중 죽어도 원본은 무사하다."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(LOG_PATH.parent), prefix=LOG_PATH.stem + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({col: r.get(col, "") for col in fieldnames})
        os.replace(tmp_path, LOG_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _append_to_csv(row: dict) -> None:
    """row를 CSV에 병합. 파일 없으면 헤더 포함 새로 만든다.

    같은 날짜가 이미 있으면 행 전체를 교체하지 않고 이번에 수집된 값만
    덮어쓴다(병합) — 그 외 컬럼(과거 NAV v2 마이그레이션이 남긴 *_v1 등)은
    보존된다. 컬럼 목록도 COLUMNS 기준으로 줄어들지 않고 기존 헤더 위에
    늘어나기만 한다. 2026-09-22 사고(23컬럼 저장으로 9개 컬럼 소멸) 재발 방지.
    """
    if LOG_PATH.exists():
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_header = reader.fieldnames or []
            rows = list(reader)
    else:
        existing_header, rows = [], []

    fieldnames = _fieldnames(existing_header, row)
    new_cols = [c for c in fieldnames if c not in existing_header]
    if new_cols:
        logger.info(f"CSV 컬럼 확장: {new_cols} 추가 (기존 컬럼은 그대로 유지)")

    idx = next((i for i, r in enumerate(rows) if r.get("date") == row.get("date")), None)
    if idx is not None:
        rows[idx] = _merge_row(rows[idx], row, fieldnames)
        logger.info(f"기존 {row.get('date')} 행 병합 갱신 (미수집 항목은 기존 값 유지)")
    else:
        rows.append(_merge_row(None, row, fieldnames))

    _atomic_write_csv(fieldnames, rows)
    logger.info(f"팩터 로그 저장: {LOG_PATH} ({date_count()} 행)")


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
