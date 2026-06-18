"""
News & Disclosure Tool (v2)
DART 공시 + Naver 뉴스 멀티쿼리 수집 → 중복제거 → 중요도 스코어링

스코어 기준 (1~5):
  5 — 공시·계약체결·실적발표 등 당일 주가 직접 변동 재료
  4 — 컨센서스 변경 유발 (가격 데이터, 동종업체 실적, 규제 초안)
  3 — 방향성 산업 시그널 (로드맵, 입법 진전, 신뢰도 있는 단독)
  2 — 해설·2차 인용
  1 — 노이즈 (필터링)
"""
import os
import re
import difflib
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from loguru import logger

# 사내망 SSL 프록시 우회
HTTP_CLIENT = httpx.Client(verify=False)

DART_BASE  = "https://opendart.fss.or.kr/api"
NAVER_BASE = "https://openapi.naver.com/v1/search/news.json"

# ── 카테고리별 쿼리 정의 ─────────────────────────────────────────────────
# (query, category, display)  display: 네이버 API 1회 fetch 건수
QUERIES = [
    # 1. 실적·수요
    ("SK하이닉스 실적",       "실적수요",   5),
    ("SK하이닉스 영업이익",   "실적수요",   3),
    ("SK하이닉스 컨센서스",   "실적수요",   3),
    ("D램 출하량",            "실적수요",   3),
    ("메모리 ASP",            "실적수요",   3),
    # 2. 제품·기술
    ("HBM4",                  "제품기술",   5),
    ("SK하이닉스 HBM",        "제품기술",   5),
    ("SK하이닉스 낸드",       "제품기술",   3),
    ("LPDDR",                 "제품기술",   3),
    ("SK하이닉스 퀄",         "제품기술",   3),
    # 3. 고객·공급계약
    ("엔비디아 HBM",          "공급계약",   5),
    ("SK하이닉스 엔비디아",   "공급계약",   5),
    ("엔비디아 실적",         "공급계약",   3),
    ("빅테크 설비투자",       "공급계약",   3),
    ("SK하이닉스 공급계약",   "공급계약",   3),
    # 4. 반도체 매크로
    ("D램 고정거래가격",      "매크로",     3),
    ("메모리 가격",           "매크로",     3),
    ("메모리 재고",           "매크로",     3),
    ("AI 데이터센터 투자",    "매크로",     3),
    ("마이크론 실적",         "매크로",     3),
    # 5. 규제
    ("반도체 수출통제",       "규제",       3),
    ("반도체 관세",           "규제",       3),
    ("SK하이닉스 우시",       "규제",       3),
    # 6. SK스퀘어 직접
    ("SK스퀘어 자사주",       "스퀘어직접", 3),
    ("SK스퀘어 주주환원",     "스퀘어직접", 3),
    ("11번가 매각",           "스퀘어직접", 3),
    ("SK스퀘어 밸류업",       "스퀘어직접", 3),
    # 7. 할인율 트리거
    ("SK그룹 지배구조",       "할인율",     3),
    ("상법 개정",             "할인율",     3),
    ("자사주 소각 의무화",    "할인율",     3),
    # 8. 중국 메모리 경쟁
    ("CXMT D램",              "중국메모리", 5),
    ("창신메모리 수율",       "중국메모리", 3),
    ("YMTC 낸드",             "중국메모리", 3),
    ("중국 메모리 가격",      "중국메모리", 3),
    ("중국 D램 수율",         "중국메모리", 3),
    ("중국 반도체 보조금",    "중국메모리", 3),
    ("JHICC 진화",            "중국메모리", 3),
    ("중국 HBM",              "중국메모리", 3),
    # 9. 대만 파운드리
    ("TSMC HBM",              "파운드리",   5),
    ("TSMC CoWoS",            "파운드리",   5),
    ("TSMC 실적",             "파운드리",   3),
    ("TSMC AI 반도체",        "파운드리",   3),
    ("대만 파운드리",         "파운드리",   3),
]

# ── NewsAPI.org 영문 쿼리 ─────────────────────────────────────────────────
NEWSAPI_QUERIES = [
    # (query, category)
    ("SK Hynix HBM memory semiconductor",                         "제품기술"),
    ("CXMT YMTC China DRAM NAND memory",                          "중국메모리"),
    ("TSMC CoWoS HBM packaging foundry",                          "파운드리"),
    ("Nvidia AMD HBM AI chip semiconductor earnings",             "공급계약"),
    ("semiconductor export control sanctions tariff memory chip", "규제"),
]

# ── 스코어링 시그널 ─────────────────────────────────────────────────────
# 영문 뉴스 스코어링 시그널 (NewsAPI)
_EN_SCORE5 = ["quarterly results", "earnings beat", "earnings miss", "supply agreement",
              "guidance raised", "guidance cut", "record revenue", "record profit",
              "contract win", "production ramp", "yield breakthrough"]
_EN_SCORE4 = ["export ban", "export control", "sanctions", "tariff", "price increase",
              "price hike", "price decline", "capex", "fixed contract", "capacity cut",
              "capacity expansion"]
_EN_NOISE  = ["is hiring", "job opening", "conference speaker", "charity",
              "donation", "award ceremony", "internship"]

_SCORE5_WORDS  = ["이사회 결의", "계약 체결", "계약체결", "공급 계약", "소각 결의",
                   "공시", "실적 발표", "가이던스", "서프라이즈", "퀄 통과", "단독 공급",
                   "분기 실적", "영업이익", "매출액"]
_SCORE4_WORDS  = ["고정거래가격", "가격 인상", "가격 하락", "컨센서스 상향", "컨센서스 하향",
                   "마이크론 실적", "엔비디아 실적", "수출 통제", "제재", "관세 발표",
                   "설비투자 확대", "capex", "캐펙스"]
_NOISE_WORDS   = ["인사", "채용", "ESG", "전시회", "부스", "행사", "후원", "봉사",
                   "협력 논의", "검토 중", "추진 검토", "의향", "기부"]
_NUMBER_PAT    = re.compile(r"\d[\d,]*\s*(%|조|억|달러|원|억원|조원|弗|달러)")


def get_news_and_disclosure(company: str, date: str) -> dict:
    """
    Args:
        company: 회사명 (e.g. 'SK스퀘어')
        date: 'YYYYMMDD'
    Returns:
        disclosures: DART 공시 목록
        news: 스코어링된 뉴스 목록 (score 내림차순)
        alerts: score 5점 기사 (즉시 보고 대상)
        overnight: 전일 22시~당일 8시 미국발 뉴스
    """
    disclosures  = _get_dart(company, date)
    naver_news   = _fetch_multi_query(date)   # 이미 중복제거·정렬됨
    newsapi_news = _fetch_newsapi(date)       # 영문, 자체 중복제거됨

    _epoch = datetime(2000, 1, 1)
    all_news = sorted(
        naver_news + newsapi_news,
        key=lambda x: (x["score"], x.get("pub_dt") or _epoch),
        reverse=True,
    )

    alerts    = [n for n in all_news if n["score"] >= 5]
    overnight = [n for n in all_news if n.get("overnight")]
    material  = [n for n in all_news if n["score"] >= 3]

    logger.info(
        f"뉴스 수집 완료 — Naver:{len(naver_news)} NewsAPI:{len(newsapi_news)} "
        f"5점:{len(alerts)} 3점이상:{len(material)} 오버나이트:{len(overnight)}"
    )

    return {
        "disclosures": disclosures,
        "news":        material,
        "alerts":      alerts,
        "overnight":   overnight,
        "all_count":   len(naver_news) + len(newsapi_news),
    }


# ── 멀티쿼리 fetch ───────────────────────────────────────────────────────

def _fetch_multi_query(date: str) -> list:
    """모든 카테고리 쿼리 실행 → 중복제거 → 스코어링 → 정렬"""
    client_id     = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id:
        return [{"title": "NAVER_CLIENT_ID 미설정", "score": 0}]

    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
    }

    # 날짜 윈도우 설정 (UTC naive — _parse_pubdate()가 naive datetime 반환하므로 맞춤)
    dt_date    = datetime.strptime(date, "%Y%m%d")
    dt_today   = datetime(dt_date.year, dt_date.month, dt_date.day)  # naive UTC
    dt_start   = dt_today - timedelta(days=1)  # 전일부터 수집
    overnight_start = dt_today - timedelta(hours=2)   # 전일 22시 KST ≈ 13:00 UTC
    overnight_end   = dt_today + timedelta(hours=8)   # 당일 08:00 KST ≈ 23:00 UTC

    raw_items = []

    import time as _time
    for query, category, display in QUERIES:
        try:
            params = {"query": query, "display": display, "sort": "date"}
            r = HTTP_CLIENT.get(NAVER_BASE, headers=headers, params=params, timeout=10)
            if r.status_code == 429:
                _time.sleep(1.0)
                r = HTTP_CLIENT.get(NAVER_BASE, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            for item in r.json().get("items", []):
                title   = BeautifulSoup(item["title"],       "html.parser").get_text()
                summary = BeautifulSoup(item["description"], "html.parser").get_text()
                pub_dt  = _parse_pubdate(item.get("pubDate", ""))

                if pub_dt and pub_dt < dt_start:
                    continue  # 전일 이전 기사 제외

                raw_items.append({
                    "title":     title,
                    "summary":   summary,
                    "pub_date":  item.get("pubDate", ""),
                    "pub_dt":    pub_dt,
                    "url":       item.get("link") or item.get("originallink", ""),
                    "category":  category,
                    "query":     query,
                    "overnight": bool(pub_dt and overnight_start <= pub_dt < overnight_end),
                })
        except Exception as e:
            logger.warning(f"뉴스 쿼리 실패 [{query}]: {e}")

    # 중복제거 → 스코어링 → 정렬
    deduped = _deduplicate(raw_items)
    scored  = [_score(item) for item in deduped]
    _epoch = datetime(2000, 1, 1)
    scored.sort(key=lambda x: (x["score"], x.get("pub_dt") or _epoch), reverse=True)
    return scored


def _deduplicate(items: list, threshold: float = 0.72) -> list:
    """제목 유사도 기반 중복 제거 (SequenceMatcher)"""
    seen_titles = []
    result      = []
    for item in items:
        norm = _normalize_title(item["title"])
        dup = any(
            difflib.SequenceMatcher(None, norm, t).ratio() >= threshold
            for t in seen_titles
        )
        if not dup:
            seen_titles.append(norm)
            result.append(item)
    return result


def _normalize_title(title: str) -> str:
    """비교용 제목 정규화 — 특수문자·공백 제거, 소문자"""
    return re.sub(r"[^\w가-힣]", "", title).lower()


def _score(item: dict) -> dict:
    """룰기반 중요도 스코어 계산"""
    text  = (item["title"] + " " + item["summary"]).lower()
    score = 2  # 기본

    # 노이즈 → 1점
    if any(w in text for w in _NOISE_WORDS):
        item["score"] = 1
        return item

    # 5점 시그널
    if any(w in text for w in _SCORE5_WORDS):
        score = max(score, 5)

    # 4점 시그널
    if any(w in text for w in _SCORE4_WORDS):
        score = max(score, 4)

    # 숫자(%, 조, 억) 포함 시 +1 (단, 이미 5점이면 유지)
    if _NUMBER_PAT.search(text) and score < 5:
        score = min(score + 1, 4)

    # 카테고리 가중치: 해당 카테고리는 최소 3점
    if item.get("category") in ("공급계약", "규제", "스퀘어직접", "중국메모리", "파운드리") and score < 3:
        score = 3

    item["score"] = score
    return item


def _parse_pubdate(pub_date_str: str):
    """RFC 2822 pubDate → datetime (UTC)"""
    try:
        return parsedate_to_datetime(pub_date_str).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _parse_iso_pubdate(pub_date_str: str):
    """ISO 8601 (NewsAPI format) → naive UTC datetime"""
    try:
        dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _score_en(item: dict) -> dict:
    """영문 뉴스 중요도 스코어링 (NewsAPI 대상)"""
    text = (item["title"] + " " + item.get("summary", "")).lower()
    if any(w in text for w in _EN_NOISE):
        item["score"] = 1
        return item
    score = 3  # 타겟 쿼리 기반 수집 → 기본 3점
    if any(w in text for w in _EN_SCORE5):
        score = 5
    elif any(w in text for w in _EN_SCORE4):
        score = max(score, 4)
    item["score"] = score
    return item


def _fetch_newsapi(date: str) -> list:
    """NewsAPI.org 영문 뉴스 수집 (중국메모리·파운드리·글로벌 반도체 중심).

    requests 사용: 사내망 SSL 패치(verify=False)와 환경변수 프록시(HTTP_PROXY 등)가
    httpx보다 안정적으로 적용됨.
    """
    import requests as _req

    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        logger.debug("NEWSAPI_KEY 미설정 — 영문 뉴스 스킵")
        return []

    dt_date  = datetime.strptime(date, "%Y%m%d")
    dt_today = datetime(dt_date.year, dt_date.month, dt_date.day)
    dt_start = dt_today - timedelta(days=2)
    overnight_start = dt_today - timedelta(hours=2)
    overnight_end   = dt_today + timedelta(hours=8)
    from_str = dt_start.strftime("%Y-%m-%dT%H:%M:%SZ")

    raw_items = []
    _fetched_queries = 0
    for query, category in NEWSAPI_QUERIES:
        try:
            params = {
                "q":        query,
                "language": "en",
                "from":     from_str,
                "sortBy":   "publishedAt",
                "pageSize": 10,
                "apiKey":   api_key,
            }
            r = _req.get("https://newsapi.org/v2/everything",
                         params=params, timeout=15)
            if not r.ok:
                body = r.json() if "application/json" in r.headers.get("content-type","") else r.text[:200]
                logger.warning(f"NewsAPI HTTP {r.status_code} [{query}]: {body}")
                # 인증 오류(401)나 플랜 제한(426)이면 더 이상 시도하지 않음
                if r.status_code in (401, 426, 429):
                    logger.error(f"NewsAPI 중단 — status={r.status_code}, 나머지 쿼리 스킵")
                    break
                continue
            _fetched_queries += 1
            for article in r.json().get("articles", []):
                title = article.get("title") or ""
                if "[Removed]" in title:
                    continue
                pub_dt = _parse_iso_pubdate(article.get("publishedAt", ""))
                raw_items.append({
                    "title":    title,
                    "summary":  article.get("description") or "",
                    "pub_date": article.get("publishedAt", ""),
                    "pub_dt":   pub_dt,
                    "url":      article.get("url", ""),
                    "category": category,
                    "query":    query,
                    "overnight": bool(pub_dt and overnight_start <= pub_dt < overnight_end),
                    "source":   article.get("source", {}).get("name", "NewsAPI"),
                })
        except Exception as e:
            logger.warning(f"NewsAPI 쿼리 실패 [{query}]: {type(e).__name__}: {e}")

    logger.info(f"NewsAPI: 쿼리 {_fetched_queries}/{len(NEWSAPI_QUERIES)}개 성공, 기사 {len(raw_items)}건 수집")
    deduped = _deduplicate(raw_items)
    return [_score_en(item) for item in deduped]


# ── DART 공시 ────────────────────────────────────────────────────────────

def _get_dart(company: str, date: str, corp_code: str = None) -> list:
    """DART 공시 목록 조회"""
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        return [{"error": "DART_API_KEY 미설정"}]

    if not corp_code:
        from memory.company_context import COMPANY_CONTEXT
        corp_code = COMPANY_CONTEXT.get("dart_corp_code")
    if not corp_code:
        return [{"error": "dart_corp_code 미설정"}]

    try:
        bgn = (datetime.strptime(date, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
        params = {
            "crtfc_key":  api_key,
            "corp_code":  corp_code,
            "bgn_de":     bgn,
            "end_de":     date,
            "page_count": 10,
        }
        r = HTTP_CLIENT.get(f"{DART_BASE}/list.json", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        if data.get("status") != "000":
            logger.warning(f"DART status: {data.get('status')} - {data.get('message')}")
            return []

        return [
            {
                "title": item.get("report_nm"),
                "time":  item.get("rcept_dt"),
                "type":  item.get("pblntf_ty"),
                "url":   f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no')}",
                "score": 5,  # 공시는 기본 5점
            }
            for item in data.get("list", [])
        ]

    except Exception as e:
        logger.error(f"DART error: {e}")
        return [{"error": str(e)}]
