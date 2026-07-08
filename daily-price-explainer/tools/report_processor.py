"""
국내 애널리스트 리포트 PDF → events.csv 이벤트 자동 추출

워크플로우:
  1. data/analyst_reports/ 에 PDF 파일 복사
  2. python main.py analyst           → 미처리 PDF 전체 분석 → data/events_staged.csv
  3. python main.py analyst --test    → 첫 번째 PDF 1개만 테스트
  4. python main.py analyst --merge   → staged 항목을 events.csv 에 병합 (중복 제거)

추적: data/.processed_reports.json (파일명 + 크기, 재처리 방지 / 중단 후 재시작 가능)
"""
import json
import os
import re
import ssl
import time
import urllib3
from pathlib import Path
from loguru import logger

# 사내망 SSL 전역 우회 (orchestrator.py 와 동일)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

try:
    import httpx
    _orig_init = httpx.Client.__init__
    def _no_verify(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        _orig_init(self, *args, **kwargs)
    httpx.Client.__init__ = _no_verify

    _orig_async = httpx.AsyncClient.__init__
    def _async_no_verify(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        _orig_async(self, *args, **kwargs)
    httpx.AsyncClient.__init__ = _async_no_verify
except ImportError:
    pass

_BASE       = Path(__file__).parent.parent
_REPORTS_DIR = _BASE / "data" / "analyst_reports"
_EVENTS_CSV  = _BASE / "data" / "events.csv"
_STAGED_CSV  = _BASE / "data" / "events_staged.csv"
_TRACKER     = _BASE / "data" / ".processed_reports.json"

_CATEGORIES = (
    "자사주/주주환원/실적발표/지배구조/신규투자/자회사"
    "/SK하이닉스/밸류업/상법개정/매크로/증권사리포트/기타"
)

_EXTRACT_PROMPT = """당신은 SK스퀘어(KOSPI 402340) IR 전문 분석가입니다.
첨부된 애널리스트 리포트에서 SK스퀘어 주가에 영향을 준 이벤트를 추출하세요.

# 추출 대상
1. 리포트 자체: 투자의견·목표주가 변경 (date = 리포트 발간일)
2. 리포트가 구체적 날짜와 함께 언급하는 회사 이벤트 (자사주·실적·공시·자회사 매각 등)
3. 거시/섹터 이벤트 (반도체 사이클, 정부 정책, 상법 개정 등) — 날짜 명시 시에만

# 날짜 규칙 (매우 중요)
- date는 반드시 YYYYMMDD 8자리 숫자만
- 날짜가 리포트 본문에 명확하게 없으면 → 그 항목은 제외
- 공시·보도가 장마감 후 → 다음 영업일

# 출력 형식 (JSON 배열만, 다른 텍스트 없이)
[
  {
    "date": "YYYYMMDD",
    "category": "카테고리",
    "event": "이벤트 한 줄 요약 (60자 이내)",
    "direction": "+또는-또는?",
    "importance": "H또는M또는L",
    "note": "보조설명 30자 이내 또는 빈문자열",
    "source": "증권사명 리포트 YYYYMMDD"
  }
]

# 필드 규칙
- category: """ + _CATEGORIES + """
- direction: +(주가 상승 요인) -(하락 요인) ?(중립)
- importance: H(시장 큰 영향) M(보통) L(참고)

# 예시
[
  {"date":"20240522","category":"자사주","event":"1000억 자사주 소각 + 추가 1000억 매입소각 선언","direction":"+","importance":"H","note":"NAV 할인율 50% 목표","source":"BNK투자증권 리포트 20240502"},
  {"date":"20240502","category":"증권사리포트","event":"BNK투자증권 목표주가 97000원으로 상향 (매수 유지)","direction":"+","importance":"M","note":"기존 87000원","source":"BNK투자증권 리포트 20240502"}
]
"""


def _load_tracker() -> dict:
    if _TRACKER.exists():
        return json.loads(_TRACKER.read_text(encoding="utf-8"))
    return {}


def _save_tracker(tracker: dict):
    _TRACKER.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_id(pdf: Path) -> str:
    """파일 식별자: 이름 + 크기 (빠른 변경 감지)"""
    return f"{pdf.name}:{pdf.stat().st_size}"


def _parse_filename(pdf: Path) -> tuple[str, str]:
    """
    파일명 패턴: 증권사명_SK스퀘어_제목_YYYYMMDD.pdf
    → (증권사명, YYYYMMDD) 반환. 파싱 실패 시 ("", "")
    """
    stem = pdf.stem  # 확장자 제거
    parts = stem.split("_")
    firm = parts[0] if parts else ""
    # 끝에서 8자리 날짜 추출
    date_match = re.search(r"(\d{8})$", stem)
    date = date_match.group(1) if date_match else ""
    return firm, date


def _load_existing_events() -> set:
    """기존 events.csv 의 (date, event 앞 20자) 조합 — 중복 방지용"""
    if not _EVENTS_CSV.exists():
        return set()
    import pandas as pd
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            df = pd.read_csv(_EVENTS_CSV, dtype=str, encoding=enc).fillna("")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return set()
    return {(row["date"].strip(), row["event"].strip()[:20])
            for _, row in df.iterrows()}


def _gemini_client():
    from dotenv import load_dotenv
    load_dotenv()
    from google import genai
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _extract_events_from_pdf(client, pdf: Path) -> str:
    """Gemini File API로 PDF 업로드 → 이벤트 추출 → CSV 텍스트 반환"""
    firm, report_date = _parse_filename(pdf)
    source_hint = f"{firm} 리포트 {report_date}" if firm and report_date else pdf.stem

    # 파일명 메타데이터를 프롬프트에 주입 (source 필드 정확도 향상)
    prompt = _EXTRACT_PROMPT + f"\n\n# 이 리포트 메타데이터\n발간 증권사: {firm}\n발간일: {report_date}\nsource 필드는 반드시 \"{source_hint}\" 형식으로 기재."

    logger.info(f"업로드 중: {pdf.name}")
    # 한글 파일명은 Gemini SDK ASCII 인코딩 오류 → BytesIO + ASCII display_name 으로 우회
    import io
    ascii_name = f"{report_date}_{firm}_report.pdf" if firm and report_date else "report.pdf"
    with open(pdf, "rb") as f:
        pdf_bytes = f.read()
    uploaded = client.files.upload(
        file=io.BytesIO(pdf_bytes),
        config={"mime_type": "application/pdf", "display_name": ascii_name},
    )

    # 처리 완료 대기 (보통 몇 초)
    for _ in range(20):
        state = str(getattr(uploaded, "state", "ACTIVE"))
        if "PROCESSING" not in state:
            break
        time.sleep(3)
        uploaded = client.files.get(name=uploaded.name)

    logger.info(f"추출 중: {pdf.name}")
    _MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]
    last_err = None
    for model in _MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=[uploaded, prompt],
            )
            break
        except Exception as e:
            last_err = e
            logger.warning(f"Gemini 오류 ({model}) — 다음 모델로: {e}")
            continue
    else:
        raise RuntimeError(f"모든 Gemini 모델 실패: {last_err}")

    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass

    return response.text


def _parse_json_response(raw: str) -> list[dict]:
    """Gemini 응답(JSON 배열) → 행 리스트. date가 YYYYMMDD 아닌 항목 제거."""
    import json
    import re

    # 코드블록 제거 후 JSON 배열 추출
    text = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    # 첫 번째 [ ... ] 블록만 파싱
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        logger.warning(f"JSON 배열 없음. 원문:\n{raw[:400]}")
        return []

    try:
        items = json.loads(match.group())
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 파싱 실패: {e}\n원문:\n{raw[:400]}")
        return []

    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date", "")).strip()
        if not re.fullmatch(r"\d{8}", date):
            logger.debug(f"날짜 형식 불일치 제외: {date!r} / {item.get('event','')}")
            continue
        valid.append({
            "date":       date,
            "category":   str(item.get("category", "기타")).strip(),
            "event":      str(item.get("event", "")).strip()[:80],
            "direction":  str(item.get("direction", "?")).strip()[:1],
            "importance": str(item.get("importance", "M")).strip()[:1],
            "note":       str(item.get("note", "")).strip()[:50],
            "source":     str(item.get("source", "")).strip(),
        })
    return valid


def process_reports(merge: bool = False, test: bool = False):
    """
    미처리 PDF → Gemini 추출 → events_staged.csv 저장.
    merge=True 면 staged를 events.csv 에 병합.
    """
    if merge:
        _merge_staged()
        return

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # Windows는 대소문자 구분이 없어 "*.pdf"와 "*.PDF"가 같은 파일을 중복으로
    # 매칭한다 (set으로 중복 제거 후 정렬)
    pdfs = sorted(set(_REPORTS_DIR.glob("*.pdf")) | set(_REPORTS_DIR.glob("*.PDF")))

    if not pdfs:
        print(f"\n[리포트 없음] data/analyst_reports/ 에 PDF를 복사한 뒤 다시 실행하세요.")
        return

    tracker = _load_tracker()
    new_pdfs = [p for p in pdfs if _file_id(p) not in tracker]

    if not new_pdfs:
        print(f"\n[신규 없음] 미처리 PDF 0건 (총 {len(pdfs)}개 이미 처리 완료)")
        print("  새 리포트를 추가하거나, --merge 로 staged 항목을 events.csv 에 병합하세요.")
        return

    if test:
        new_pdfs = new_pdfs[:1]
        print(f"\n[테스트 모드] 1개만 처리: {new_pdfs[0].name}")
    else:
        print(f"\n신규 리포트 {len(new_pdfs)}건 처리 시작 (중단 시 재실행하면 이어서 처리됩니다)...")
    client = _gemini_client()
    existing = _load_existing_events()

    import pandas as pd
    cols = ["date", "category", "event", "direction", "importance", "note", "source"]
    total_saved = 0
    for pdf in new_pdfs:
        try:
            raw = _extract_events_from_pdf(client, pdf)
            rows = _parse_json_response(raw)
            # 기존 events.csv 중복 제거
            new_rows = [
                r for r in rows
                if (r.get("date","").strip(), r.get("event","").strip()[:20]) not in existing
            ]
            logger.info(f"  {pdf.name}: {len(rows)}건 추출, {len(new_rows)}건 신규")

            # PDF 1개 처리할 때마다 즉시 저장 (중단 시 유실 방지)
            if new_rows:
                df_new = pd.DataFrame(new_rows).reindex(columns=cols).fillna("")
                if _STAGED_CSV.exists():
                    for enc in ("utf-8-sig", "cp949", "utf-8"):
                        try:
                            df_existing = pd.read_csv(_STAGED_CSV, dtype=str, encoding=enc).fillna("")
                            df_new = pd.concat([df_existing, df_new], ignore_index=True)
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                df_new = df_new.drop_duplicates(subset=["date", "event"]).sort_values("date").reset_index(drop=True)
                df_new.to_csv(_STAGED_CSV, index=False, encoding="utf-8-sig")
                total_saved = len(df_new)

            tracker[_file_id(pdf)] = pdf.name
            _save_tracker(tracker)
        except Exception as e:
            logger.error(f"  {pdf.name} 처리 실패: {e}")

    if total_saved:
        print(f"\n  → data/events_staged.csv 에 총 {total_saved}건 저장 완료")
        print("  내용 확인 후: python main.py analyst --merge")
    else:
        print("\n  신규 이벤트 없음 (모두 기존 events.csv 와 중복)")


def _merge_staged():
    """events_staged.csv → events.csv 병합 (중복 제거, 날짜순 정렬)"""
    import pandas as pd

    if not _STAGED_CSV.exists():
        print("\n[병합할 파일 없음] 먼저 python main.py analyst 를 실행하세요.")
        return

    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            staged = pd.read_csv(_STAGED_CSV, dtype=str, encoding=enc).fillna("")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise ValueError(f"events_staged.csv 인코딩 인식 실패: {_STAGED_CSV}")
    print(f"\nstaged 항목: {len(staged)}건")

    if _EVENTS_CSV.exists():
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                existing = pd.read_csv(_EVENTS_CSV, dtype=str, encoding=enc).fillna("")
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            existing = pd.DataFrame()
        combined = pd.concat([existing, staged], ignore_index=True)
    else:
        combined = staged

    combined["date"] = combined["date"].str.strip()
    combined = (combined
                .drop_duplicates(subset=["date", "event"])
                .sort_values("date")
                .reset_index(drop=True))

    combined.to_csv(_EVENTS_CSV, index=False, encoding="utf-8-sig")
    _STAGED_CSV.unlink()
    print(f"  → events.csv 업데이트 완료: 총 {len(combined)}건")
    print("  python main.py events 로 분석 결과 확인하세요.")
