"""
Orchestrator Agent
질문 의도 분류 → 툴 선택 → 병렬 호출 → Claude로 답변 합성
"""
import os
import ssl
import asyncio
import urllib3
import httpx
from datetime import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv
from loguru import logger

# 사내망 SSL 전역 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

# httpx 전역 패치 (Gemini SDK 포함)
_orig_httpx_init = httpx.Client.__init__
def _httpx_no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _httpx_no_verify

_orig_async_init = httpx.AsyncClient.__init__
def _async_no_verify(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _orig_async_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _async_no_verify

from tools.market import get_market_data
from tools.news import get_news_and_disclosure
from tools.macro import get_macro_data
from tools.sector import get_sector_comparison
from tools.nav import get_nav_data
from tools.sector_rotation import get_sector_rotation
from tools.broker import get_broker_data
from tools.nxt import get_nxt_data
from tools.futures import get_futures_data
from tools.short import get_shorting_data
from memory.cache import cache_get, cache_set, make_key
from memory.company_context import get_company_context, COMPANY_CONTEXT
from prompts.system_prompt import build_system_prompt

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 질문 의도 분류 키워드 맵
INTENT_KEYWORDS = {
    "daily_briefing": ["브리핑", "요약", "오늘 주가", "전체", "정리"],
    "supply_demand":  ["수급", "외국인", "기관", "개인", "순매수", "매도", "창구", "증권사"],
    "news":           ["뉴스", "공시", "기사", "발표", "공고"],
    "macro":          ["환율", "금리", "유가", "매크로", "글로벌", "미국", "VIX"],
    "sector":         ["섹터", "경쟁사", "업종", "대비", "비교", "하이닉스"],
    "nav":            ["NAV", "할인율", "순자산", "하이닉스 지분", "자산가치"],
    "rotation":       ["로테이션", "섹터 흐름", "반도체", "조선", "방산", "바이오", "어느 섹터"],
    "history":        ["전에", "이전", "지난", "과거", "패턴", "비슷"],
    "futures":        ["선물", "미결제약정", "베이시스", "콘탱고", "백워데이션", "OI", "파생"],
}


def classify_intent(query: str) -> list[str]:
    """질문에서 필요한 툴 목록 반환"""
    query_lower = query.lower()
    matched = []
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(intent)

    # 매칭 없으면 기본 브리핑
    if not matched:
        matched = ["daily_briefing"]

    # 브리핑이면 모든 툴
    if "daily_briefing" in matched:
        return ["market", "news", "macro", "sector", "nav", "rotation", "broker", "nxt", "futures", "short"]

    # 의도별 툴 매핑
    tool_map = {
        "supply_demand": ["market", "broker"],
        "news":          ["news"],
        "macro":         ["macro", "market"],
        "sector":        ["sector", "market"],
        "nav":           ["nav", "market"],
        "rotation":      ["rotation"],
        "history":       ["market"],
        "futures":       ["futures", "market"],
    }
    tools = set()
    for intent in matched:
        tools.update(tool_map.get(intent, ["market"]))

    # market이 포함되면 nxt·broker 항상 추가
    if "market" in tools:
        tools.update(["nxt", "broker"])

    return list(tools)


def _cached_call(tool_fn, key: str, *args):
    cached = cache_get(key)
    if cached:
        logger.debug(f"Cache hit: {key}")
        return cached
    result = tool_fn(*args)
    cache_set(key, result)
    return result


async def collect_data(ticker: str, company: str, date: str,
                       tools: list[str]) -> dict:
    """선택된 툴들을 비동기 병렬 실행"""
    loop = asyncio.get_event_loop()
    tasks = {}

    if "market" in tools:
        key = make_key("market", ticker=ticker, date=date)
        tasks["market"] = loop.run_in_executor(
            None, _cached_call, get_market_data, key, ticker, date)

    if "news" in tools:
        key = make_key("news", company=company, date=date)
        tasks["news"] = loop.run_in_executor(
            None, _cached_call, get_news_and_disclosure, key, company, date)

    if "macro" in tools:
        key = make_key("macro", date=date)
        tasks["macro"] = loop.run_in_executor(
            None, _cached_call, get_macro_data, key, date)

    if "sector" in tools:
        competitors = COMPANY_CONTEXT.get("competitors", [])
        key = make_key("sector", ticker=ticker, date=date)
        tasks["sector"] = loop.run_in_executor(
            None, _cached_call, get_sector_comparison,
            key, ticker, date, competitors)

    if "nav" in tools:
        key = make_key("nav", ticker=ticker, date=date)
        tasks["nav"] = loop.run_in_executor(
            None, _cached_call, get_nav_data, key, date)

    if "rotation" in tools:
        key = make_key("rotation", date=date)
        tasks["rotation"] = loop.run_in_executor(
            None, _cached_call, get_sector_rotation, key, date)

    if "broker" in tools:
        key = make_key("broker", ticker=ticker)
        tasks["broker"] = loop.run_in_executor(
            None, _cached_call, get_broker_data, key, ticker)

    if "nxt" in tools:
        key = make_key("nxt", ticker=ticker)
        tasks["nxt"] = loop.run_in_executor(
            None, _cached_call, get_nxt_data, key, ticker)

    if "futures" in tools:
        key = make_key("futures", ticker=ticker, date=date)
        tasks["futures"] = loop.run_in_executor(
            None, _cached_call, get_futures_data, key, ticker, date)

    if "short" in tools:
        key = make_key("short", ticker=ticker, date=date)
        tasks["short"] = loop.run_in_executor(
            None, _cached_call, get_shorting_data, key, ticker, date)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return dict(zip(tasks.keys(), results))


def build_context(data: dict, query: str) -> str:
    """수집 데이터 → 프롬프트 컨텍스트 조립"""
    import pytz
    kst = pytz.timezone("Asia/Seoul")
    now_kst = datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")
    lines = [get_company_context(), "", f"## 수집 데이터 (조회 시각: {now_kst})", ""]

    if "market" in data and isinstance(data["market"], dict):
        m = data["market"]
        is_prev = m.get("is_prev_day", False)
        date_label = f"전일({m.get('date','')}) 종가 기준" if is_prev else "당일 기준"
        lines.append(f"### 주가 데이터 ({date_label})")
        pct = m.get('pct_change')
        pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"
        vol = m.get('volume')
        vol_str = f"{vol:,}" if isinstance(vol, (int, float)) else "N/A"
        close_label = "전일 종가" if is_prev else "종가"
        lines.append(f"- {close_label}: {m.get('close', 'N/A')}원 ({pct_str})")
        lines.append(f"- 거래량: {vol_str}")
        if "investor_flow" in m:
            f = m["investor_flow"]
            def _fmt_flow(v, unit="원"):
                if v is None:
                    return "데이터 없음"
                return f"{v:+,}{unit}"
            lines.append(f"- 외국인 순매수: {_fmt_flow(f.get('foreign_net'))}")
            lines.append(f"- 기관 순매수: {_fmt_flow(f.get('institution_net'))}")
            lines.append(f"- 개인 순매수: {_fmt_flow(f.get('individual_net'))}")
        if "kospi_investor_flow" in m:
            kf = m["kospi_investor_flow"]
            lines.append(f"- [KOSPI 전체] 외국인 순매수: {kf.get('foreign_net', 0):+,}억원")
            lines.append(f"- [KOSPI 전체] 기관 순매수: {kf.get('institution_net', 0):+,}억원")
            lines.append(f"- [KOSPI 전체] 개인 순매수: {kf.get('individual_net', 0):+,}억원")
        lines.append("")

    if "news" in data and isinstance(data["news"], dict):
        nd = data["news"]

        # ── 공시 ──────────────────────────────────────────────
        lines.append("### 공시 (DART)")
        disclosures = nd.get("disclosures", [])
        if disclosures and not disclosures[0].get("error"):
            for item in disclosures[:5]:
                lines.append(f"- [공시] {item.get('title', '')} ({item.get('time', '')})")
        else:
            lines.append("- 공시 없음")
        lines.append("")

        # ── 즉시 알림 뉴스 (5점) ──────────────────────────────
        alerts = nd.get("alerts", [])
        if alerts:
            lines.append("### ⚡ 주가 직접 영향 뉴스")
            for item in alerts[:5]:
                url = item.get('url', '')
                lines.append(
                    f"- [{item.get('category','뉴스')}] {item['title']} "
                    f"| {item.get('summary','')[:100]}"
                    + (f" | URL: {url}" if url else "")
                )
            lines.append("")

        # ── 오버나이트 미국발 뉴스 ────────────────────────────
        overnight = nd.get("overnight", [])
        overnight_material = [n for n in overnight if n.get("score", 0) >= 3
                              and n not in alerts]
        if overnight_material:
            lines.append("### 🌙 오버나이트 미국발 뉴스 (전일 22시~당일 8시)")
            for item in overnight_material[:3]:
                url = item.get('url', '')
                lines.append(
                    f"- [{item.get('category','뉴스')}] {item['title']} "
                    f"| {item.get('summary','')[:100]}"
                    + (f" | URL: {url}" if url else "")
                )
            lines.append("")

        # ── 일반 뉴스 (카테고리별) ────────────────────────────
        material = [n for n in nd.get("news", [])
                    if n.get("score", 0) in (3, 4) and n not in alerts]
        if material:
            lines.append("### 주요 뉴스 (카테고리별)")
            by_cat = {}
            for item in material[:15]:
                cat = item.get("category", "기타")
                by_cat.setdefault(cat, []).append(item)
            for cat, items in by_cat.items():
                for item in items[:2]:
                    url = item.get('url', '')
                    lines.append(
                        f"- [{cat}] {item['title']} "
                        f"| {item.get('summary','')[:80]}"
                        + (f" | URL: {url}" if url else "")
                    )
        lines.append("")

    if "macro" in data and isinstance(data["macro"], dict):
        macro = data["macro"]
        lines.append("### 매크로 지표")
        if "usd_krw" in macro:
            lines.append(f"- USD/KRW: {macro['usd_krw']}")
        if "kospi" in macro:
            k = macro["kospi"]
            kp = k.get('pct_change')
            kp_str = f"{kp:+.2f}%" if isinstance(kp, (int, float)) else "N/A"
            lines.append(f"- KOSPI: {k.get('close')} ({kp_str})")
        if "sp500" in macro:
            s = macro["sp500"]
            sp = s.get('pct_change')
            sp_str = f"{sp:+.2f}%" if isinstance(sp, (int, float)) else "N/A"
            lines.append(f"- S&P500: {s.get('close')} ({sp_str})")
        if "vix" in macro:
            lines.append(f"- VIX: {macro['vix'].get('close')}")
        if "wti" in macro:
            lines.append(f"- WTI 유가: {macro['wti'].get('close')}")
        # 매크로 관련 뉴스: 규제·매크로(업종 전반) 카테고리
        _append_related_news(lines, data, cats=("규제", "매크로"), max_items=3)
        lines.append("")

    if "sector" in data and isinstance(data["sector"], dict):
        sec = data["sector"]
        lines.append("### 섹터 비교")
        rel = sec.get('relative_performance')
        rel_str = f"{rel:+.2f}%" if isinstance(rel, (int, float)) else "N/A"
        lines.append(f"- KOSPI 대비 상대 수익률: {rel_str}")
        for name, pct in sec.get("competitors", {}).items():
            pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"
            lines.append(f"- {name}: {pct_str}")
        # 섹터 관련 뉴스: 업종 방향성 + 중국 메모리 경쟁 + 파운드리 공급망
        _append_related_news(lines, data, cats=("매크로", "규제", "중국메모리", "파운드리"), max_items=4)
        lines.append("")

    if "nav" in data and isinstance(data["nav"], dict):
        nav = data["nav"]
        lines.append("### NAV 분석 (Value 팩터)")
        lines.append(f"- 총 NAV: {nav.get('nav_total_trillion', 'N/A')}조원")
        lines.append(f"- 시가총액: {nav.get('market_cap_trillion', 'N/A')}조원")
        disc = nav.get('nav_discount_pct')
        disc_str = f"{disc:+.1f}%" if isinstance(disc, (int, float)) else "N/A"
        lines.append(f"- NAV 할인율: {disc_str}")
        delta      = nav.get('nav_discount_delta')
        trend_dir  = nav.get('discount_trend_dir')
        trend_days = nav.get('discount_trend_days')
        if delta is not None:
            # delta < 0 = 할인율 감소 = 축소 (한국 IR 관행: 할인율 = (NAV-시총)/NAV, 양수=할인)
            trend_label = "할인율 축소(재평가)" if delta < 0 else "할인율 확대(디스카운트 심화)"
            trend_streak = (f", {trend_dir} {trend_days}일째 지속"
                            if trend_dir and trend_days else "")
            lines.append(f"- NAV 할인율 전일 대비: {delta:+.1f}%p ({trend_label}{trend_streak})")

        # ── 역사적 NAV 할인율 통계 (factor_log.csv) ─────────────────────
        try:
            from tools.factor_logger import get_discount_stats
            ds = get_discount_stats(windows=[20, 60])
            n_days = ds.get("n_days", 0)
            if n_days >= 5:
                lines.append(f"- [NAV 할인율 역사 통계] 누적 {n_days}거래일")
                stats = ds.get("stats", {})
                for w in [20, 60]:
                    if w in stats:
                        s = stats[w]
                        lines.append(
                            f"  · 최근 {w}일: 평균 {s['mean']:+.1f}% "
                            f"(범위 {s['min']:+.1f}% ~ {s['max']:+.1f}%)"
                        )
                hist_trend = ds.get("trend")
                if hist_trend:
                    lines.append(f"  · 최근 5일 추세: 할인율 {hist_trend}")
        except Exception as _e:
            logger.debug(f"discount_stats 로드 생략: {_e}")

        skq_pct     = nav.get('skq_pct')
        nav_imp_pct = nav.get('nav_implied_pct')
        divergence  = nav.get('divergence')
        if skq_pct is not None and nav_imp_pct is not None:
            lines.append(f"- SK스퀘어 실제 수익률: {skq_pct:+.2f}%")
            lines.append(f"- NAV 기반 기대 수익률: {nav_imp_pct:+.2f}% (할인율 유지 가정)")
            lines.append(f"- 수익률 괴리(잔차): {divergence:+.2f}%p "
                         f"({'NAV 대비 초과 상승 → 할인율 축소 or 독립 모멘텀' if divergence > 0.5 else 'NAV 대비 초과 하락 → 할인율 확대 or 독립 매도압력' if divergence < -0.5 else 'NAV 연동 정상 범위'})")
        for h in nav.get("hynix_nav_change", []):
            pct = h.get('pct_change')
            impact = h.get('nav_impact_trillion')
            per_share = h.get('nav_impact_per_share')
            lines.append(
                f"- {h['name']} {pct:+.2f}% → NAV {impact:+.2f}조 / 주당 {per_share:+,.0f}원"
            )
        # NAV 관련 뉴스: 제목에 SK하이닉스·SK스퀘어가 명시된 기사만 (삼성전자·업종 전반 제외)
        _append_related_news(lines, data,
                             cats=("실적수요", "제품기술", "공급계약", "스퀘어직접", "할인율"),
                             max_items=5,
                             must_contain=("SK하이닉스", "SK스퀘어", "하이닉스"))
        lines.append("")

    if "rotation" in data and isinstance(data["rotation"], dict):
        rot = data["rotation"]
        sig = rot.get("rotation_signal", {})
        lines.append("### 섹터 로테이션")
        lines.append(f"- 패턴: {sig.get('pattern', 'N/A')}")
        for sector, pct in sig.get("all_sectors_ranked", []):
            pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"
            lines.append(f"- {sector}: {pct_str}")
        lines.append("")

    if "nxt" in data and isinstance(data["nxt"], dict) and data["nxt"]:
        n = data["nxt"]
        pct = n.get("pct_change")
        pct_str = f"{pct:+.2f}%" if isinstance(pct, float) else "N/A"
        lines.append("### NXT(넥스트레이드) 현재가")
        lines.append(f"- NXT 현재가: {n.get('price', 'N/A'):,}원 ({pct_str})")
        lines.append(f"- 방향: {n.get('direction', 'N/A')}")
        lines.append("")

    if "futures" in data and isinstance(data["futures"], dict):
        fut = data["futures"]
        if fut.get("listed") and "near_month" in fut:
            nm = fut["near_month"]
            basis = fut.get("basis")
            basis_pct = fut.get("basis_pct")
            oi_total = fut.get("open_interest_total")
            oi_chg = fut.get("oi_change")
            lines.append("### 주식선물 (파생 팩터)")
            if nm.get("close"):
                lines.append(f"- 최근월물({nm.get('futures_ticker', 'N/A')}) 종가: {nm['close']:,}원")
            if basis is not None:
                basis_label = "콘탱고(매수차익)" if basis > 0 else "백워데이션(매도차익)" if basis < 0 else "등가"
                lines.append(f"- 베이시스: {basis:+,}원 ({basis_pct:+.3f}%) — {basis_label}")

                # ── 차익거래 압력 해석 ─────────────────────────────────────
                skq_pct = None
                if "market" in data and isinstance(data["market"], dict):
                    skq_pct = data["market"].get("pct_change")
                if basis is not None and skq_pct is not None:
                    if basis > 0 and skq_pct < -0.3:
                        lines.append(
                            "- [차익거래 해석] 콘탱고(선물>현물)에서 현물 하락 "
                            "→ 매도차익거래 압력 의심 (현물 매도·선물 매수)"
                        )
                    elif basis < 0 and skq_pct > 0.3:
                        lines.append(
                            "- [차익거래 해석] 백워데이션(선물<현물) 심화 "
                            "→ 숏커버링 또는 현물 선호 매수 압력 (선물 대비 현물 초강세)"
                        )
                    elif basis > 0 and skq_pct > 0.3:
                        lines.append(
                            "- [차익거래 해석] 콘탱고 + 현물 상승 → 정상 프리미엄, 차익거래 압력 없음"
                        )
                    elif basis < 0 and skq_pct < -0.3:
                        lines.append(
                            "- [차익거래 해석] 백워데이션(선물<현물)에서 현물 하락 "
                            "→ 선물·현물 동반 약세, 방향성 매도 또는 헤지 가능"
                        )

            if oi_total is not None:
                lines.append(f"- 전체 미결제약정: {oi_total:,}계약"
                             + (f" (전일 대비 {oi_chg:+,})" if oi_chg is not None else ""))
            lines.append("")
        elif not fut.get("listed"):
            lines.append("### 주식선물")
            lines.append("- 미상장 또는 데이터 없음")
            lines.append("")

    if "broker" in data and isinstance(data["broker"], dict):
        b = data["broker"]
        # 종가 × 주식수 → 억원 거래대금 변환 (Naver 거래원 테이블은 주식수만 제공)
        close = data["market"].get("close") if isinstance(data.get("market"), dict) else None

        def _vol_to_value(volume, signed=False) -> str:
            if not isinstance(volume, int):
                return "N/A"
            if close:
                val = volume * close / 1e8
                return f"{val:+,.1f}억원" if signed else f"{val:,.1f}억원"
            return f"{volume:+,}주" if signed else f"{volume:,}주"  # fallback

        lines.append("### 거래원 (창구별 순매수)")
        fn = b.get("foreign_net")
        if fn is not None:
            lines.append(f"- 외국계 추정 순매수: {_vol_to_value(fn, signed=True)}")
        sellers = b.get("top_sellers", [])
        buyers  = b.get("top_buyers", [])
        if sellers:
            lines.append("- 매도 상위: " + ", ".join(
                f"{s['name']}({_vol_to_value(s['volume'])})" for s in sellers))
        if buyers:
            lines.append("- 매수 상위: " + ", ".join(
                f"{s['name']}({_vol_to_value(s['volume'])})" for s in buyers))
        # 수급 배경 뉴스: 거시경제·규제 흐름이 외국인·기관 수급에 영향
        _append_related_news(lines, data, cats=("규제", "매크로"), max_items=2)
        lines.append("")

    if "short" in data and isinstance(data["short"], dict):
        sh = data["short"]
        bal_date = sh.get("balance_date", "전일")
        lines.append(f"### 공매도 현황 ({bal_date} 기준, T+1 공시)")
        bal = sh.get("shorting_balance")
        bal_ratio = sh.get("shorting_balance_ratio")
        vol_ratio = sh.get("shorting_volume_ratio")
        chg = sh.get("balance_change")
        signal = sh.get("signal")

        if bal is not None:
            lines.append(f"- 공매도 잔고: {bal:,}주 (잔고율 {bal_ratio}%)")
        if chg is not None:
            chg_label = "증가 → 공매도 압력↑" if chg > 0 else "감소 → 숏커버링(매수 유입 가능)"
            lines.append(f"- 전전일 대비 잔고 변화: {chg:+,}주 ({chg_label})")
        if vol_ratio is not None:
            lines.append(f"- 전일 공매도 비중: {vol_ratio}% (총거래량 대비)")
        if signal:
            signal_desc = {
                "압력": "공매도 잔고 증가 + 잔고율 ≥1% → 하방 압력 구조",
                "청산": "공매도 잔고 감소 → 숏커버링 매수 유입 가능",
                "중립": "공매도 변화 미미",
            }.get(signal, signal)
            lines.append(f"- 시그널: {signal_desc}")
        if not any([bal, vol_ratio]):
            lines.append("- 공매도 데이터 미수집 (KRX 데이터 지연 가능)")
        lines.append("")

    lines.append(f"## 사용자 질문\n{query}")
    return "\n".join(lines)


def _append_related_news(lines: list, data: dict,
                         cats: tuple, max_items: int = 3,
                         must_contain: tuple = None) -> None:
    """섹션 하단에 해당 카테고리 뉴스를 인라인 추가.

    must_contain: 제목에 이 키워드 중 하나라도 포함되어야 함 (None이면 제한 없음).
    NAV 섹션처럼 특정 종목 뉴스만 허용할 때 사용.
    """
    nd = data.get("news")
    if not isinstance(nd, dict):
        return
    all_news = nd.get("alerts", []) + nd.get("news", [])
    matched = [n for n in all_news if n.get("category") in cats and n.get("score", 0) >= 3]

    if must_contain:
        matched = [n for n in matched
                   if any(kw in n.get("title", "") for kw in must_contain)]

    # 중복 제거 (alerts가 news에도 있을 수 있음)
    seen, unique = set(), []
    for n in matched:
        key = n.get("title", "")
        if key not in seen:
            seen.add(key)
            unique.append(n)
    if unique:
        lines.append("- [관련 뉴스]")
        for n in unique[:max_items]:
            url = n.get('url', '')
            lines.append(
                f"  · [{n.get('category')}] {n['title']}"
                + (f" | URL: {url}" if url else "")
            )


def chat(query: str, date: str = None) -> str:
    """메인 엔트리포인트"""
    if date is None:
        date = datetime.today().strftime("%Y%m%d")

    ticker = os.getenv("COMPANY_TICKER", "402340")
    company = os.getenv("COMPANY_NAME", "SK스퀘어")

    # 1. 의도 분류
    tools = classify_intent(query)
    logger.info(f"Intent: {tools}")

    # 2. 데이터 수집
    data = asyncio.run(collect_data(ticker, company, date, tools))

    # 3. 주가 데이터로 시스템 프롬프트 구성
    market = data.get("market", {})
    system = build_system_prompt(
        company_name=company,
        ticker=ticker,
        date=date,
        close_price=market.get("close", 0),
        pct_change=market.get("pct_change", 0),
    )

    # 4. 컨텍스트 조립 + Gemini 호출
    # 모델 우선순위: 2.5-flash → 2.0-flash → 1.5-flash (503 폴백)
    import time
    _MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-8b", "gemini-1.5-flash"]
    context = build_context(data, query)
    last_err = None
    for model in _MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    config=types.GenerateContentConfig(system_instruction=system),
                    contents=context,
                )
                if model != "gemini-2.5-flash":
                    logger.info(f"폴백 모델 사용: {model}")
                return response.text
            except Exception as e:
                last_err = e
                if "503" in str(e) and attempt < 2:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Gemini 503 ({model}) — {wait}초 후 재시도 ({attempt+1}/3)")
                    time.sleep(wait)
                elif "503" in str(e):
                    logger.warning(f"{model} 3회 모두 503 — 다음 모델로 폴백")
                    break  # 다음 모델 시도
                else:
                    raise
    raise last_err
