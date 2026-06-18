"""
Sector Rotation Factor Tool

동일 섹터의 여러 운용사 ETF를 평균내어 섹터별 등락률 계산.
단일 ETF의 구성종목 편향을 제거하고 시장 컨센서스 기반 섹터 수익률 산출.

섹터 등락률 = 해당 섹터 ETF들의 단순 평균 등락률
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

from pykrx import stock
from loguru import logger

# ─────────────────────────────────────────────
# 섹터별 ETF 리스트 (운용사 무관, 동일 테마 묶음)
# ticker: KRX ETF 종목코드
# ─────────────────────────────────────────────
SECTOR_ETF_MAP = {
    "반도체": [
        {"ticker": "091160", "name": "KODEX 반도체",       "manager": "삼성"},
        {"ticker": "091230", "name": "TIGER 반도체",       "manager": "미래에셋"},
        {"ticker": "396510", "name": "HANARO 반도체",      "manager": "NH"},
    ],
    "2차전지": [
        {"ticker": "305720", "name": "KODEX 2차전지산업",  "manager": "삼성"},
        {"ticker": "305540", "name": "TIGER 2차전지테마",  "manager": "미래에셋"},
        {"ticker": "철수확인", "name": "KINDEX 배터리",    "manager": "한국투자"},  # 상폐 여부 확인 필요
    ],
    "조선": [
        {"ticker": "139230", "name": "KODEX 조선&기계",    "manager": "삼성"},
        {"ticker": "139260", "name": "TIGER 조선TOP10",    "manager": "미래에셋"},
    ],
    "방산": [
        {"ticker": "490090", "name": "TIGER 방산",         "manager": "미래에셋"},
        {"ticker": "425040", "name": "KODEX K-방산",       "manager": "삼성"},
    ],
    "바이오": [
        {"ticker": "244580", "name": "KODEX 바이오",       "manager": "삼성"},
        {"ticker": "143460", "name": "TIGER 200 헬스케어", "manager": "미래에셋"},
    ],
    "금융/은행": [
        {"ticker": "091170", "name": "KODEX 은행",         "manager": "삼성"},
        {"ticker": "091220", "name": "TIGER 은행",         "manager": "미래에셋"},
    ],
    "AI/IT": [
        {"ticker": "487620", "name": "TIGER AI코리아",     "manager": "미래에셋"},
        {"ticker": "364980", "name": "KODEX AI&로봇액티브","manager": "삼성"},
    ],
    "에너지/화학": [
        {"ticker": "117460", "name": "KODEX 에너지화학",   "manager": "삼성"},
        {"ticker": "139270", "name": "TIGER 200 에너지화학","manager": "미래에셋"},
    ],
}


def get_sector_rotation(date: str) -> dict:
    """
    Args:
        date: 'YYYYMMDD'
    Returns:
        sectors: 섹터별 평균 등락률 및 구성 ETF 상세
        rotation_signal: 상위/하위 섹터 요약 (경영진 보고용)
    """
    from tools.market import _prev_trading_day
    prev_date = _prev_trading_day(date)

    sector_results = {}

    for sector_name, etf_list in SECTOR_ETF_MAP.items():
        etf_returns = []
        etf_details = []

        for etf in etf_list:
            ticker = etf["ticker"]
            if len(ticker) != 6 or not ticker.isdigit():
                continue  # 유효하지 않은 ticker 스킵

            try:
                today = stock.get_etf_ohlcv_by_date(prev_date, date, ticker)
                if today is None or today.empty or len(today) < 2:
                    # ETF 전용 조회 실패 시 일반 종목으로 fallback
                    today_ohlcv = stock.get_market_ohlcv(date, date, ticker)
                    prev_ohlcv  = stock.get_market_ohlcv(prev_date, prev_date, ticker)
                    if today_ohlcv.empty or prev_ohlcv.empty:
                        continue
                    today_price = int(today_ohlcv.iloc[0]["종가"])
                    prev_price  = int(prev_ohlcv.iloc[0]["종가"])
                else:
                    today_price = int(today.iloc[-1]["종가"])
                    prev_price  = int(today.iloc[-2]["종가"])

                pct = round((today_price / prev_price - 1) * 100, 2)
                etf_returns.append(pct)
                etf_details.append({
                    "name": etf["name"],
                    "manager": etf["manager"],
                    "pct_change": pct,
                })

            except Exception as e:
                logger.debug(f"{etf['name']} ({ticker}) 조회 실패: {e}")
                continue

        if etf_returns:
            # 이상치 제거: 3개 이상일 때 평균±2σ 벗어나는 ETF 제외
            filtered_returns, filtered_details = _remove_outliers(etf_returns, etf_details)
            avg_return = round(sum(filtered_returns) / len(filtered_returns), 2)
            sector_results[sector_name] = {
                "avg_pct_change": avg_return,
                "etf_count": len(filtered_returns),
                "etf_details": filtered_details,
            }
            logger.info(f"[{sector_name}] 평균 {avg_return:+.2f}% ({len(filtered_returns)}개 ETF, 이상치 제거 후)")

    # ── 로테이션 시그널 생성 ──
    rotation_signal = _build_rotation_signal(sector_results)

    return {
        "date": date,
        "sectors": sector_results,
        "rotation_signal": rotation_signal,
    }


def _remove_outliers(returns: list, details: list) -> tuple:
    """3개 이상 ETF일 때 평균±2σ 벗어나는 이상치 제거"""
    if len(returns) < 3:
        return returns, details

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = variance ** 0.5

    filtered = [(r, d) for r, d in zip(returns, details)
                if abs(r - mean) <= 2 * std]

    if not filtered:
        return returns, details  # 전부 제거되면 원본 유지

    f_returns, f_details = zip(*filtered)
    removed = len(returns) - len(f_returns)
    if removed:
        logger.debug(f"이상치 {removed}개 제거: {[d['name'] for r, d in zip(returns, details) if abs(r - mean) > 2 * std]}")

    return list(f_returns), list(f_details)


def _build_rotation_signal(sectors: dict) -> dict:
    """상위/하위 섹터 정렬 및 로테이션 패턴 감지"""
    if not sectors:
        return {}

    sorted_sectors = sorted(
        sectors.items(),
        key=lambda x: x[1]["avg_pct_change"],
        reverse=True,
    )

    top_2    = [(k, v["avg_pct_change"]) for k, v in sorted_sectors[:2]]
    bottom_2 = [(k, v["avg_pct_change"]) for k, v in sorted_sectors[-2:]]

    # 로테이션 강도: 상위 섹터 - 하위 섹터 스프레드
    spread = round(top_2[0][1] - bottom_2[-1][1], 2) if top_2 and bottom_2 else 0

    # 로테이션 방향 판단
    all_positive = all(v["avg_pct_change"] > 0 for v in sectors.values())
    all_negative = all(v["avg_pct_change"] < 0 for v in sectors.values())

    if all_positive:
        pattern = "전 섹터 상승 (Risk-on)"
    elif all_negative:
        pattern = "전 섹터 하락 (Risk-off)"
    elif spread >= 5:
        pattern = f"강한 로테이션: {top_2[0][0]} → {bottom_2[-1][0]}"
    elif spread >= 2:
        pattern = f"완만한 로테이션: {top_2[0][0]} 강세 / {bottom_2[-1][0]} 약세"
    else:
        pattern = "섹터 간 차별화 미미"

    return {
        "pattern": pattern,
        "spread_pct": spread,
        "top_sectors": top_2,
        "bottom_sectors": bottom_2,
        "all_sectors_ranked": [(k, v["avg_pct_change"]) for k, v in sorted_sectors],
    }
