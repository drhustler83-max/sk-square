"""
NAV Factor Tool
SK스퀘어 NAV(순자산가치) 계산 및 NAV 할인율 산출

NAV = Σ(상장 자회사 지분가치) + Σ(비상장 자회사 장부가)
NAV 할인율 = (시가총액 - NAV) / NAV × 100
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
from memory.company_context import COMPANY_CONTEXT


def get_nav_data(date: str) -> dict:
    """
    Args:
        date: 'YYYYMMDD'
    Returns:
        nav_total: 총 NAV (원)
        market_cap: 시가총액 (원)
        nav_discount_pct: NAV 할인율 (%) — 음수면 할인, 양수면 프리미엄
        holdings_breakdown: 자산별 기여 내역
        hynix_contribution: SK하이닉스 지분가치 변동이 NAV에 미친 영향
    """
    ctx = COMPANY_CONTEXT
    holdings = ctx.get("nav_holdings", [])
    shares_outstanding = ctx.get("shares_outstanding", 0)
    ticker = ctx["ticker"]

    holdings_breakdown = []
    nav_total = 0

    # 1. 상장 자회사 지분가치 계산
    for h in holdings:
        if h["listed"] and h["ticker"]:
            try:
                ohlcv = stock.get_market_ohlcv(date, date, h["ticker"])
                _price_date = date
                if ohlcv.empty:
                    from tools.market import _prev_trading_day
                    _price_date = _prev_trading_day(date)
                    ohlcv = stock.get_market_ohlcv(_price_date, _price_date, h["ticker"])
                    if ohlcv.empty:
                        logger.warning(f"No data for {h['name']} on {date} or {_price_date}")
                        continue
                    logger.info(f"{h['name']} 오늘 데이터 없음 → 전일({_price_date}) 종가 사용")
                price = int(ohlcv.iloc[0]["종가"])
                value = price * h["shares_held"]
                nav_total += value
                holdings_breakdown.append({
                    "name": h["name"],
                    "type": "상장",
                    "price": price,
                    "shares_held": h["shares_held"],
                    "value": value,
                    "value_trillion": round(value / 1e12, 2),
                })
                logger.info(f"{h['name']}: {price:,}원 × {h['shares_held']:,}주 = {value/1e12:.2f}조원")
            except Exception as e:
                logger.warning(f"NAV {h['name']} error: {e}")

        elif not h["listed"]:
            # 비상장: 장부가 사용
            book_val = h.get("book_value", 0)
            nav_total += book_val
            holdings_breakdown.append({
                "name": h["name"],
                "type": "비상장(장부가)",
                "value": book_val,
                "value_trillion": round(book_val / 1e12, 2),
            })

    # 2. 자사 시가총액
    market_cap = 0
    try:
        ohlcv = stock.get_market_ohlcv(date, date, ticker)
        if not ohlcv.empty:
            price = int(ohlcv.iloc[0]["종가"])
            market_cap = price * shares_outstanding
    except Exception as e:
        logger.warning(f"Market cap error: {e}")

    # 3. NAV 할인율
    nav_discount_pct = None
    if nav_total > 0 and market_cap > 0:
        nav_discount_pct = round((nav_total - market_cap) / nav_total * 100, 1)  # 양수 = 할인 (Korean IR 관행)

    # 4. 전일 NAV 할인율 계산 → 할인율 델타 + 연속 추세 일수
    nav_discount_delta = None
    discount_trend_days = None   # 연속 추세 일수 (양수)
    discount_trend_dir  = None   # "축소" | "확대" | "보합"
    try:
        from tools.market import _prev_trading_day
        prev_date = _prev_trading_day(date)
        prev_nav, prev_mcap = _calc_prev_nav(prev_date, holdings, ticker)
        if prev_nav > 0 and prev_mcap > 0:
            prev_discount = round((prev_nav - prev_mcap) / prev_nav * 100, 1)
            if nav_discount_pct is not None:
                nav_discount_delta = round(nav_discount_pct - prev_discount, 1)
                # 방향: delta 음수 = 할인율 감소 = 축소 (한국 IR 관행: 양수=할인)
                if abs(nav_discount_delta) < 0.1:
                    discount_trend_dir = "보합"
                elif nav_discount_delta < 0:
                    discount_trend_dir = "축소"
                else:
                    discount_trend_dir = "확대"

                # 연속 추세 일수 계산 (최대 5영업일 소급)
                discount_trend_days = _calc_trend_days(
                    date, nav_discount_pct, holdings, ticker, max_lookback=5
                )

                logger.info(
                    f"NAV 할인율: 전일 {prev_discount:+.1f}% → 오늘 {nav_discount_pct:+.1f}% "
                    f"(Δ{nav_discount_delta:+.1f}%p, {discount_trend_dir} {discount_trend_days}일째)"
                )
    except Exception as e:
        logger.warning(f"NAV discount delta 계산 오류: {e}")

    # 5. 상장 자산별 전일 대비 NAV 변동 기여도
    hynix_contribution = _calc_listed_nav_change(date, holdings)

    # 6. SK스퀘어 실제 수익률 vs NAV 기반 기대 수익률 괴리 계산
    skq_pct = None          # 전일 데이터 결손 시에도 return에서 UnboundLocalError 방지
    nav_implied_pct = None
    divergence = None
    try:
        from tools.market import _prev_trading_day
        prev_date = _prev_trading_day(date)
        skq_today = stock.get_market_ohlcv(date, date, ticker)
        skq_prev  = stock.get_market_ohlcv(prev_date, prev_date, ticker)
        if not skq_today.empty and not skq_prev.empty:
            skq_t = int(skq_today.iloc[0]["종가"])
            skq_p = int(skq_prev.iloc[0]["종가"])
            skq_prev_mcap = skq_p * shares_outstanding
            skq_today_mcap = skq_t * shares_outstanding
            skq_prev_nav, _ = _calc_prev_nav(prev_date, holdings, ticker)

            skq_actual_chg = skq_today_mcap - skq_prev_mcap
            # NAV 변동 기반 기대 시총 변동 (전일 시총/NAV 비율 유지 가정)
            nav_chg = nav_total - skq_prev_nav if skq_prev_nav > 0 else 0
            if skq_prev_mcap > 0 and skq_prev_nav > 0:
                # 기대 수익률 = NAV 변동률 (할인율 고정 가정)
                # 논리: 할인율 불변이면 시총 변동률 = NAV 변동률
                nav_implied_pct = round(nav_chg / skq_prev_nav * 100, 2)
                skq_pct = round((skq_t / skq_p - 1) * 100, 2)
                divergence = round(skq_pct - nav_implied_pct, 2)
                logger.info(
                    f"SK스퀘어 실제 {skq_pct:+.2f}% vs NAV기대 {nav_implied_pct:+.2f}% "
                    f"→ 괴리 {divergence:+.2f}%p"
                )
    except Exception as e:
        logger.warning(f"괴리 계산 오류: {e}")

    return {
        "date": date,
        "nav_total": nav_total,
        "nav_total_trillion": round(nav_total / 1e12, 2),
        "market_cap": market_cap,
        "market_cap_trillion": round(market_cap / 1e12, 2),
        "nav_discount_pct": nav_discount_pct,
        "nav_discount_delta": nav_discount_delta,      # 할인율 전일 대비 변동 (△%p, 음수=축소)
        "discount_trend_dir":  discount_trend_dir,     # "축소" | "확대" | "보합"
        "discount_trend_days": discount_trend_days,    # 연속 일수
        "skq_pct": skq_pct,                            # SK스퀘어 실제 수익률
        "nav_implied_pct": nav_implied_pct,            # NAV 기반 기대 수익률
        "divergence": divergence,                      # 괴리 (양수 = 실제>기대)
        "holdings_breakdown": holdings_breakdown,
        "hynix_nav_change": hynix_contribution,
    }


def _calc_prev_nav(prev_date: str, holdings: list, ticker: str) -> tuple:
    """전일 NAV 및 시가총액 계산"""
    shares_outstanding = COMPANY_CONTEXT.get("shares_outstanding", 1)
    prev_nav = 0
    for h in holdings:
        if h["listed"] and h["ticker"]:
            try:
                ohlcv = stock.get_market_ohlcv(prev_date, prev_date, h["ticker"])
                if not ohlcv.empty:
                    price = int(ohlcv.iloc[0]["종가"])
                    prev_nav += price * h["shares_held"]
            except Exception:
                pass
        elif not h["listed"]:
            prev_nav += h.get("book_value", 0)

    prev_mcap = 0
    try:
        ohlcv = stock.get_market_ohlcv(prev_date, prev_date, ticker)
        if not ohlcv.empty:
            prev_mcap = int(ohlcv.iloc[0]["종가"]) * shares_outstanding
    except Exception:
        pass

    return prev_nav, prev_mcap


def _calc_listed_nav_change(date: str, holdings: list) -> list:
    """상장 자산별 주가 변동이 SK스퀘어 NAV에 미친 영향 계산"""
    from tools.market import _prev_trading_day

    shares_outstanding = COMPANY_CONTEXT.get("shares_outstanding", 1)
    prev_date = _prev_trading_day(date)
    results = []

    for h in [x for x in holdings if x["listed"] and x.get("ticker")]:
        try:
            today = stock.get_market_ohlcv(date, date, h["ticker"])
            prev  = stock.get_market_ohlcv(prev_date, prev_date, h["ticker"])
            if today.empty or prev.empty:
                continue

            today_price = int(today.iloc[0]["종가"])
            prev_price  = int(prev.iloc[0]["종가"])
            price_change = today_price - prev_price
            pct_change   = round((today_price / prev_price - 1) * 100, 2)
            nav_change   = price_change * h["shares_held"]

            results.append({
                "name": h["name"],
                "ticker": h["ticker"],
                "price_today": today_price,
                "price_change": price_change,
                "pct_change": pct_change,
                "nav_impact": nav_change,
                "nav_impact_trillion": round(nav_change / 1e12, 2),
                "nav_impact_per_share": round(nav_change / shares_outstanding, 0),
            })
            logger.info(
                f"{h['name']} {pct_change:+.2f}% → "
                f"NAV {nav_change/1e12:+.3f}조 / 주당 {nav_change/shares_outstanding:+,.0f}원"
            )
        except Exception as e:
            logger.warning(f"{h['name']} NAV change error: {e}")

    return results


def _calc_trend_days(date: str, today_discount: float,
                     holdings: list, ticker: str, max_lookback: int = 5) -> int:
    """
    NAV 할인율 연속 추세 일수 계산.
    today_discount < 전일 → 축소 방향으로 몇 일째 연속인지.
    returns: 연속 일수 (최소 1)
    """
    from tools.market import _prev_trading_day
    try:
        discounts = [today_discount]
        d = date
        for _ in range(max_lookback):
            d = _prev_trading_day(d)
            nav, mcap = _calc_prev_nav(d, holdings, ticker)
            if nav > 0 and mcap > 0:
                discounts.append(round((nav - mcap) / nav * 100, 1))
            else:
                break

        if len(discounts) < 2:
            return 1

        # 오늘 방향 결정
        shrinking = discounts[0] < discounts[1]  # True = 축소 추세

        # 연속 카운트
        days = 1
        for i in range(1, len(discounts) - 1):
            is_same = (discounts[i] < discounts[i + 1]) == shrinking
            if is_same:
                days += 1
            else:
                break
        return days
    except Exception as e:
        logger.warning(f"trend_days 계산 오류: {e}")
        return 1
