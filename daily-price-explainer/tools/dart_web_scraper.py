"""
DART 전자공시 + SK스퀘어 홈페이지 → events_staged.csv 이벤트 수집

사용:
  python main.py dart-web          → DART + 홈페이지 모두
  python main.py dart-web --dart   → DART만
  python main.py dart-web --web    → 홈페이지만

수집 범위:
  DART  : SK스퀘어 전체 공시 (2021-11-01 ~ 오늘), 중요 유형 필터링
  홈페이지: sksquare.com IR자료실 + 뉴스룸 전체 목록
"""
import os
import re
import ssl
import time
import urllib3
from datetime import date
from pathlib import Path
from loguru import logger

# 사내망 SSL 전역 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

try:
    import httpx as _httpx
    _orig = _httpx.Client.__init__
    def _nv(self, *a, **kw):
        kw.setdefault("verify", False)
        _orig(self, *a, **kw)
    _httpx.Client.__init__ = _nv
except ImportError:
    pass

_BASE       = Path(__file__).parent.parent
_STAGED_CSV = _BASE / "data" / "events_staged.csv"
_EVENTS_CSV = _BASE / "data" / "events.csv"

CORP_CODE = "01596425"
DART_BASE = "https://opendart.fss.or.kr/api"

# ── DART 공시 유형 → (category, direction, importance) 매핑 ──────────────
_DART_RULES = [
    (["자기주식 취득결정", "자기주식취득"],           "자사주",   "+", "H"),
    (["자기주식 처분결정", "자기주식처분"],           "자사주",   "+", "M"),
    (["자기주식 소각결정", "자기주식소각"],           "자사주",   "+", "H"),
    (["주주환원", "배당결정", "현금배당", "현물배당"], "주주환원", "+", "H"),
    (["기업가치 제고", "밸류업"],                     "밸류업",   "+", "H"),
    (["정기주주총회", "임시주주총회", "주주총회"],    "주주환원", "+", "M"),
    (["사업보고서"],                                  "실적발표", "?", "M"),
    (["반기보고서"],                                  "실적발표", "?", "M"),
    (["분기보고서"],                                  "실적발표", "?", "M"),
    (["매출액 또는 손익구조"],                        "실적발표", "+", "H"),
    (["합병", "분할", "분리"],                        "지배구조", "?", "H"),
    (["주식의 포괄적 교환", "주식교환"],              "지배구조", "?", "H"),
    (["종속회사", "관계회사", "자회사"],              "자회사",   "?", "M"),
    (["임원변경", "대표이사"],                        "지배구조", "?", "M"),
    (["전환사채", "신주인수권부사채", "교환사채"],    "지배구조", "?", "M"),
    (["유상증자", "무상증자"],                        "지배구조", "?", "M"),
]


# ── 카테고리 분류 함수 (WEB_SECTIONS보다 먼저 정의) ───────────────────────

def _cat_ir(title: str) -> tuple:
    t = title.lower()
    if "portfolio update" in t or "실적" in t:
        return "실적발표", "?", "M"
    if "자기주식" in t or "자사주" in t:
        return "자사주", "+", "H"
    if "주주환원" in t or "배당" in t:
        return "주주환원", "+", "H"
    if "기업가치" in t or "밸류업" in t or "value" in t:
        return "밸류업", "+", "H"
    if "손익구조" in t or "매출액" in t:
        return "실적발표", "+", "H"
    return "기타", "?", "L"


def _cat_news(title: str) -> tuple:
    if any(k in title for k in ["자사주", "자기주식"]):
        return "자사주", "+", "H"
    if any(k in title for k in ["주주환원", "배당", "소각"]):
        return "주주환원", "+", "H"
    if any(k in title for k in ["기업가치", "밸류업"]):
        return "밸류업", "+", "H"
    if any(k in title for k in ["실적", "영업이익", "매출"]):
        return "실적발표", "?", "M"
    if any(k in title for k in ["합병", "분할", "매각", "IPO"]):
        return "자회사", "?", "H"
    if any(k in title for k in ["상법", "지배구조"]):
        return "상법개정", "?", "M"
    return "기타", "?", "L"


# ── 웹사이트 섹션 목록 (함수 정의 이후에 배치) ────────────────────────────
_WEB_SECTIONS = [
    {
        "name":       "IR자료실",
        "url":        "https://www.sksquare.com/kor/ir/presentation.do",
        "max_pages":  6,
        "category_fn": _cat_ir,
    },
    {
        "name":       "뉴스룸",
        "url":        "https://www.sksquare.com/kor/news/newsMediaList.do",
        "max_pages":  20,
        "category_fn": _cat_news,
    },
]


# ── DART 수집 ──────────────────────────────────────────────────────────────

def fetch_dart_events() -> list:
    """DART API → SK스퀘어 공시 이벤트 리스트"""
    from dotenv import load_dotenv
    load_dotenv()
    import httpx

    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        logger.error("DART_API_KEY 미설정 — .env 확인")
        return []

    client = httpx.Client(verify=False, timeout=20)
    today  = date.today().strftime("%Y%m%d")
    rows   = []

    # 연도별 청크 (API 한번에 최대 100건)
    year_chunks = []
    for y in range(2021, int(today[:4]) + 1):
        bgn = f"{y}1101" if y == 2021 else f"{y}0101"
        end = today if y == int(today[:4]) else f"{y}1231"
        year_chunks.append((bgn, end))

    logger.info(f"DART 수집 시작: {len(year_chunks)}개 연도 구간")

    for bgn, end in year_chunks:
        page = 1
        while True:
            params = {
                "crtfc_key":  api_key,
                "corp_code":  CORP_CODE,
                "bgn_de":     bgn,
                "end_de":     end,
                "page_count": 100,
                "page_no":    page,
            }
            try:
                r = client.get(f"{DART_BASE}/list.json", params=params)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                logger.warning(f"DART 오류 ({bgn}~{end} p{page}): {e}")
                break

            if data.get("status") != "000":
                logger.debug(f"DART {bgn}~{end}: status={data.get('status')}")
                break

            for item in data.get("list", []):
                row = _classify_dart(item)
                if row:
                    rows.append(row)

            total = int(data.get("total_count", 0))
            if page * 100 >= total:
                break
            page += 1
            time.sleep(0.2)

        logger.info(f"  {bgn}~{end} 처리 완료, 누적 {len(rows)}건")

    logger.info(f"DART 수집 완료: {len(rows)}건")
    return rows


def _classify_dart(item: dict):
    report_nm = item.get("report_nm", "")
    rcept_dt  = item.get("rcept_dt", "")
    rcept_no  = item.get("rcept_no", "")

    if not re.fullmatch(r"\d{8}", rcept_dt):
        return None

    category, direction, importance = "기타", "?", "L"
    matched = False
    for keywords, cat, direc, imp in _DART_RULES:
        if any(kw in report_nm for kw in keywords):
            category, direction, importance = cat, direc, imp
            matched = True
            break

    if not matched:
        return None

    return {
        "date":       rcept_dt,
        "category":   category,
        "event":      report_nm[:60],
        "direction":  direction,
        "importance": importance,
        "note":       "",
        "source":     f"DART {rcept_dt} rcpNo={rcept_no}",
    }


# ── SK스퀘어 홈페이지 수집 ────────────────────────────────────────────────

def fetch_web_events() -> list:
    """SK스퀘어 홈페이지 IR자료실 + 뉴스룸 → 이벤트 리스트"""
    import httpx
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    client = httpx.Client(verify=False, timeout=20, follow_redirects=True, headers=headers)
    rows   = []

    for section in _WEB_SECTIONS:
        name     = section["name"]
        base_url = section["url"]
        cat_fn   = section["category_fn"]
        logger.info(f"홈페이지 {name} 수집 (최대 {section['max_pages']}p)")

        seen = set()
        for pg in range(1, section["max_pages"] + 1):
            try:
                # pageIndex 방식 우선 시도, POST도 함께 준비
                r = client.get(base_url, params={"pageIndex": pg, "currentPage": pg})
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
            except Exception as e:
                logger.warning(f"  {name} p{pg} 오류: {e}")
                break

            items = _parse_board(soup)
            if not items:
                logger.info(f"  {name}: p{pg} 항목 없음 → 종료")
                break

            new_count = 0
            for title, date_str in items:
                if title in seen:
                    continue
                seen.add(title)

                dt = _norm_date(date_str)
                if not dt or dt < "20211101":
                    continue

                cat, direc, imp = cat_fn(title)
                rows.append({
                    "date":       dt,
                    "category":   cat,
                    "event":      title[:60],
                    "direction":  direc,
                    "importance": imp,
                    "note":       "",
                    "source":     f"SK스퀘어 홈페이지 {name} {dt}",
                })
                new_count += 1

            logger.debug(f"  {name} p{pg}: {new_count}건 신규")
            time.sleep(0.4)

        # 중복 체크: 연속 2페이지가 동일하면 페이지네이션 없는 것 → 1페이지만 수집
        _dedup_section(rows, name)

    logger.info(f"홈페이지 수집 완료: {len(rows)}건")
    return rows


def _dedup_section(rows: list, name: str):
    """같은 source 이름에서 중복 이벤트 제거 (페이지네이션 미동작 시 방어)"""
    keys = set()
    to_remove = []
    for i, r in enumerate(rows):
        if name not in r.get("source", ""):
            continue
        key = (r["date"], r["event"][:20])
        if key in keys:
            to_remove.append(i)
        else:
            keys.add(key)
    for i in reversed(to_remove):
        rows.pop(i)


def _parse_board(soup) -> list:
    """
    SK스퀘어 게시판 HTML 파서.
    여러 셀렉터를 순서대로 시도하여 (title, date_str) 리스트 반환.
    """
    results = []

    # 시도 0: ul.board-list > li.board-list-item (뉴스룸 전용)
    for li in soup.select("ul.board-list li.board-list-item, ul.board-list li"):
        date_div = li.select_one("div.date, span.date, .date")
        date_str = date_div.get_text(strip=True) if date_div else ""
        # 제목: a 태그 → 제목 div → board-list-item-contents 첫 텍스트
        title = ""
        for sel in ("a.tit", "p.tit", "div.tit", "a", "p", "h4", "h3", "strong"):
            el = li.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if len(t) > 5 and t != date_str:
                    title = t
                    break
        if not title:
            # board-list-item-contents 에서 날짜 제외한 첫 텍스트
            contents = li.select_one("div.board-list-item-contents")
            if contents:
                full = contents.get_text(separator="\n", strip=True)
                for line in full.splitlines():
                    if len(line) > 5 and line != date_str and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
                        title = line
                        break
        if title and date_str:
            results.append((title, date_str))

    if results:
        return results

    # 시도 1: dl/dt/dd 구조 (SK스퀘어 IR 페이지)
    for dl in soup.select("dl"):
        dt_tags  = dl.select("dt, .tit, .title, .subject, a")
        dd_tags  = dl.select("dd, .date, .reg-date, span.date")
        title    = _best_text(dt_tags, min_len=5)
        date_str = _best_text(dd_tags, pattern=r"\d{4}")
        if title and date_str:
            results.append((title, date_str))

    if results:
        return results

    # 시도 2: 일반 ul/li 구조
    for li in soup.select("ul.list li, .news-list li, .ir-list li"):
        spans    = li.select("span, p, a, strong")
        title    = _best_text(spans, min_len=5)
        date_spans = li.select(".date, .reg-date, time, em")
        date_str = _best_text(date_spans, pattern=r"\d{4}")
        if not date_str:
            date_str = _extract_date_from_text(li.get_text())
        if title and date_str:
            results.append((title, date_str))

    if results:
        return results

    # 시도 3: 테이블 행 (IR자료실 fallback)
    for tr in soup.select("table tbody tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        title    = ""
        date_str = ""
        for td in tds:
            txt = td.get_text(strip=True)
            if re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", txt):
                date_str = txt
            elif len(txt) > 5 and not txt.isdigit():
                if len(txt) > len(title):
                    title = txt
        if title and date_str:
            results.append((title, date_str))

    return results


def _best_text(tags, min_len: int = 0, pattern: str = None) -> str:
    for tag in tags:
        txt = tag.get_text(strip=True)
        if len(txt) >= min_len:
            if pattern is None or re.search(pattern, txt):
                return txt
    return ""


def _extract_date_from_text(text: str) -> str:
    m = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", text)
    return m.group() if m else ""


def _norm_date(s: str) -> str:
    """YYYY-MM-DD / YYYY.MM.DD / YYYYMMDD → YYYYMMDD. 실패 시 ''"""
    clean = re.sub(r"[-./\s]", "", s.strip())
    return clean if re.fullmatch(r"\d{8}", clean) else ""


# ── staged CSV 저장 ────────────────────────────────────────────────────────

def _existing_keys() -> set:
    """events.csv + events_staged.csv 의 (date, event[:20]) 집합"""
    keys = set()
    for path in [_EVENTS_CSV, _STAGED_CSV]:
        if not path.exists():
            continue
        import pandas as pd
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                df = pd.read_csv(path, dtype=str, encoding=enc).fillna("")
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            continue
        for _, r in df.iterrows():
            keys.add((r.get("date", "").strip(), r.get("event", "").strip()[:20]))
    return keys


def _save_staged(rows: list) -> int:
    import pandas as pd

    existing = _existing_keys()
    new = []
    for r in rows:
        key = (r.get("date", "").strip(), r.get("event", "").strip()[:20])
        if key not in existing:
            new.append(r)
            existing.add(key)

    if not new:
        logger.info("신규 이벤트 없음 (모두 중복)")
        return 0

    cols = ["date", "category", "event", "direction", "importance", "note", "source"]
    df_new = pd.DataFrame(new).reindex(columns=cols).fillna("")

    if _STAGED_CSV.exists():
        try:
            df_old = pd.read_csv(_STAGED_CSV, dtype=str, encoding="cp949").fillna("")
            df_new = pd.concat([df_old, df_new], ignore_index=True)
        except Exception:
            pass

    df_new = df_new.sort_values("date").reset_index(drop=True)
    df_new.to_csv(_STAGED_CSV, index=False, encoding="cp949")
    logger.info(f"events_staged.csv 저장: {len(new)}건 신규")
    return len(new)


# ── 진입점 ─────────────────────────────────────────────────────────────────

def run(dart_only: bool = False, web_only: bool = False):
    all_rows = []

    if not web_only:
        d = fetch_dart_events()
        all_rows.extend(d)
        print(f"DART 공시:        {len(d)}건")

    if not dart_only:
        w = fetch_web_events()
        all_rows.extend(w)
        print(f"SK스퀘어 홈페이지: {len(w)}건")

    saved = _save_staged(all_rows)
    print(f"\n→ events_staged.csv 에 신규 {saved}건 추가")
    print("  검토 후: python main.py analyst --merge")
