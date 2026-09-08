"""Structured analyst target-price extraction kept separate from free-form events.

This module deliberately uses its own CSV files and processing tracker so the
same PDF can be processed independently by the legacy events pipeline and the
structured target-price pipeline.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from loguru import logger

from tools.report_processor import (
    _BASE,
    _REPORTS_DIR,
    _file_id,
    _gemini_client,
    _parse_filename,
)


_TARGETS_CSV = _BASE / "data" / "analyst_targets.csv"
_TARGETS_STAGED_CSV = _BASE / "data" / "analyst_targets_staged.csv"
_TARGETS_TRACKER = _BASE / "data" / ".processed_analyst_targets.json"

_TARGET_COLUMNS = [
    "report_date",
    "broker",
    "target_price_krw",
    "prev_target_price_krw",
    "direction",
    "change_pct",
    "opinion",
    "evidence",
    "source_file",
    "source_file_id",
    "extraction_method",
    "needs_review",
    "review_reason",
]



_TARGET_RESPONSE_KEYS = [
    "target_price_krw",
    "prev_target_price_krw",
    "investment_opinion",
    "evidence",
    "extraction_note",
]

_TARGET_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "target_price_krw": {"type": "INTEGER", "nullable": True},
        "prev_target_price_krw": {"type": "INTEGER", "nullable": True},
        "investment_opinion": {"type": "STRING", "nullable": True},
        "evidence": {"type": "STRING", "nullable": True},
        "extraction_note": {"type": "STRING", "nullable": True},
    },
    "required": _TARGET_RESPONSE_KEYS,
}

_TARGET_PROMPT = """
당신은 국내 증권사 리포트의 목표주가 표를 전사하는 데이터 추출기입니다.
첨부 PDF에 명시된 SK스퀘어(402340) 현재 목표주가와 직전 목표주가,
투자의견, 근거 원문을 아래 JSON 객체로만 반환하세요.

- 금액은 원화 정수로 변환하세요. 확인할 수 없으면 null입니다.
- prev_target_price_krw는 PDF에 명시된 직전/기존 목표가만 사용하고 추정하지 마세요.
- evidence는 목표주가와 직전 목표주가가 보이는 PDF 원문을 짧게 그대로 옮기세요.
- 목표주가가 없는 리포트면 target_price_krw를 null로 반환하세요.
- 방향(up/down 등)을 판단하거나 출력하지 마세요. 방향은 두 숫자로 코드가 계산합니다.

반환 키는 반드시 다음 다섯 개이며 모두 포함하세요:
target_price_krw, prev_target_price_krw, investment_opinion, evidence, extraction_note
"""

_TARGET_EXTRACTION_METHOD = "gemini_structured_v1"
_GEMINI_REVIEW_REASON = "Gemini-only extraction; PDF verification required"
_MIN_TARGET_PRICE_KRW = 1_000
_MAX_TARGET_PRICE_KRW = 10_000_000


class TargetValidationError(ValueError):
    """Hard validation failure that must not be written to the target CSV."""


def _load_target_tracker() -> dict:
    if _TARGETS_TRACKER.exists():
        try:
            data = json.loads(_TARGETS_TRACKER.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            logger.warning(
                f"목표주가 처리 트래커를 읽지 못해 빈 트래커로 시작: {_TARGETS_TRACKER}"
            )
    return {}


def _save_target_tracker(tracker: dict):
    _TARGETS_TRACKER.parent.mkdir(parents=True, exist_ok=True)
    _TARGETS_TRACKER.write_text(
        json.dumps(tracker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _validate_target_filename(pdf: Path) -> tuple[str, str]:
    """Validate broker and date returned only by the legacy filename parser."""
    from datetime import datetime

    broker, report_date = _parse_filename(pdf)
    if not broker:
        raise TargetValidationError("filename broker missing")
    if not re.fullmatch(r"\d{8}", report_date or ""):
        raise TargetValidationError("filename report_date must be YYYYMMDD")
    try:
        datetime.strptime(report_date, "%Y%m%d")
    except ValueError as exc:
        raise TargetValidationError(
            "filename report_date is not a valid calendar date"
        ) from exc
    return broker, report_date


def _extract_target_from_pdf(client, pdf: Path) -> str:
    """Make exactly one structured Gemini generate call for one PDF."""
    import io

    broker, report_date = _validate_target_filename(pdf)
    prompt = (
        _TARGET_PROMPT
        + f"\n\n# 파일명 메타데이터\n증권사: {broker}\n발간일: {report_date}"
        + "\n증권사와 발간일은 JSON으로 반환하지 마세요."
    )
    uploaded = None
    try:
        with open(pdf, "rb") as handle:
            pdf_bytes = handle.read()
        uploaded = client.files.upload(
            file=io.BytesIO(pdf_bytes),
            config={
                "mime_type": "application/pdf",
                "display_name": f"{report_date}_analyst_report.pdf",
            },
        )

        for _ in range(20):
            state = str(getattr(uploaded, "state", "ACTIVE"))
            if "PROCESSING" not in state:
                break
            time.sleep(3)
            uploaded = client.files.get(name=uploaded.name)

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded, prompt],
            config={
                "response_mime_type": "application/json",
                "response_schema": _TARGET_RESPONSE_SCHEMA,
            },
        )
        return response.text
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def _parse_target_response(raw: str) -> dict:
    """Parse a single JSON object, including an optionally fenced response."""
    if not isinstance(raw, str):
        raise TargetValidationError("response is not text")
    text = (
        re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE)
        .strip()
        .rstrip("`")
        .strip()
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise TargetValidationError("JSON object not found")
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise TargetValidationError(f"invalid JSON object: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise TargetValidationError("response JSON must be an object")
    return payload


def _korean_price_candidates(text: str) -> set[int]:
    """Extract prices such as 97,000원, 9.7만원, 9만7000원, or 9만 7천원."""
    if not isinstance(text, str):
        return set()
    compact = re.sub(r"[\s,]", "", text).replace("₩", "원")
    candidates: set[int] = set()

    for match in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)만(?:(\d+)천|(\d+))?원?",
        compact,
    ):
        man_text, cheon_text, rest_text = match.groups()
        try:
            if "." in man_text and (cheon_text or rest_text):
                continue
            value = int(round(float(man_text) * 10_000))
            if cheon_text:
                value += int(cheon_text) * 1_000
            elif rest_text:
                value += int(rest_text)
            candidates.add(value)
        except ValueError:
            continue

    for match in re.finditer(r"(?<![\d.])(\d+)원", compact):
        try:
            candidates.add(int(match.group(1)))
        except ValueError:
            continue
    # Frequent table form: target price (KRW): 97,000.
    for match in re.finditer(r'(?<![\d.])(\d{4,8})(?![\d.])', compact):
        try:
            candidates.add(int(match.group(1)))
        except ValueError:
            continue
    return candidates


def _evidence_contains_price(price: int, evidence: str) -> bool:
    return price in _korean_price_candidates(evidence)


def _validate_target_payload(payload: dict) -> dict:
    """Validate types, ranges, and evidence while ignoring unknown keys."""
    if not isinstance(payload, dict):
        raise TargetValidationError("target payload must be an object")
    missing = [key for key in _TARGET_RESPONSE_KEYS if key not in payload]
    if missing:
        raise TargetValidationError(f"missing required keys: {', '.join(missing)}")

    current = payload["target_price_krw"]
    previous = payload["prev_target_price_krw"]
    for field, value in (
        ("target_price_krw", current),
        ("prev_target_price_krw", previous),
    ):
        # bool is an int subclass, so use an exact type check.
        if value is not None and type(value) is not int:
            raise TargetValidationError(f"{field} must be a JSON integer or null")
        if value is not None and not (
            _MIN_TARGET_PRICE_KRW <= value <= _MAX_TARGET_PRICE_KRW
        ):
            raise TargetValidationError(
                f"{field} outside allowed range "
                f"{_MIN_TARGET_PRICE_KRW:,}..{_MAX_TARGET_PRICE_KRW:,}"
            )

    for field in ("investment_opinion", "evidence", "extraction_note"):
        value = payload[field]
        if value is not None and not isinstance(value, str):
            raise TargetValidationError(f"{field} must be a string or null")

    # A null current target is an ordinary no-target result.
    if current is None:
        return {key: payload[key] for key in _TARGET_RESPONSE_KEYS}

    evidence = (payload["evidence"] or "").strip()
    if not evidence:
        raise TargetValidationError(
            "evidence is required when target_price_krw is present"
        )
    if not _evidence_contains_price(current, evidence):
        raise TargetValidationError("target_price_krw not found in evidence")
    if previous is not None and not _evidence_contains_price(previous, evidence):
        raise TargetValidationError("prev_target_price_krw not found in evidence")

    normalized = {key: payload[key] for key in _TARGET_RESPONSE_KEYS}
    normalized["investment_opinion"] = (
        normalized["investment_opinion"] or ""
    ).strip()
    normalized["evidence"] = evidence
    normalized["extraction_note"] = (
        normalized["extraction_note"] or ""
    ).strip()
    return normalized


def _target_direction(current: int | None, previous: int | None) -> str:
    if current is None or previous is None:
        return "unknown"
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "unchanged"


def _build_target_row(pdf: Path, payload: dict) -> dict | None:
    """Build a CSV row; return None for a normal no-target payload."""
    broker, report_date = _validate_target_filename(pdf)
    data = _validate_target_payload(payload)
    current = data["target_price_krw"]
    previous = data["prev_target_price_krw"]
    if current is None:
        return None

    note = data["extraction_note"]
    review_reason = _GEMINI_REVIEW_REASON
    if note:
        review_reason += f"; extraction_note: {note}"

    change_pct = (
        "" if previous is None else round((current / previous - 1) * 100, 4)
    )
    return {
        "report_date": report_date,
        "broker": broker,
        "target_price_krw": current,
        "prev_target_price_krw": "" if previous is None else previous,
        "direction": _target_direction(current, previous),
        "change_pct": change_pct,
        "opinion": data["investment_opinion"],
        "evidence": data["evidence"],
        "source_file": pdf.name,
        "source_file_id": _file_id(pdf),
        "extraction_method": _TARGET_EXTRACTION_METHOD,
        "needs_review": True,
        "review_reason": review_reason,
    }


def _read_csv_with_fallback(path: Path):
    import pandas as pd

    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding).fillna("")
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"CSV 인코딩 인식 실패: {path}")


def _append_target_staged(row: dict) -> int:
    """Append one validated row and deduplicate by source_file_id, keeping last."""
    import pandas as pd

    new_frame = pd.DataFrame([row]).reindex(columns=_TARGET_COLUMNS).fillna("")
    if _TARGETS_STAGED_CSV.exists():
        existing = _read_csv_with_fallback(_TARGETS_STAGED_CSV)
        combined = pd.concat([existing, new_frame], ignore_index=True)
    else:
        combined = new_frame
    combined = (
        combined.reindex(columns=_TARGET_COLUMNS)
        .fillna("")
        .drop_duplicates(subset=["source_file_id"], keep="last")
        .sort_values(["report_date", "broker", "source_file"])
        .reset_index(drop=True)
    )
    _TARGETS_STAGED_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(_TARGETS_STAGED_CSV, index=False, encoding="utf-8-sig")
    return len(combined)


def _record_target_status(
    tracker: dict,
    pdf: Path,
    status: str,
    review_reason: str = "",
):
    tracker[_file_id(pdf)] = {
        "source_file": pdf.name,
        "status": status,
        "review_reason": review_reason,
    }
    _save_target_tracker(tracker)


def process_target_reports(merge: bool = False, test: bool = False):
    """Process untracked PDFs into the dedicated staged target-price CSV."""
    if merge:
        _merge_target_staged()
        return

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(set(_REPORTS_DIR.glob("*.pdf")) | set(_REPORTS_DIR.glob("*.PDF")))
    if not pdfs:
        print("\n[리포트 없음] data/analyst_reports/ 에 PDF를 복사한 뒤 다시 실행하세요.")
        return

    tracker = _load_target_tracker()
    new_pdfs = [pdf for pdf in pdfs if _file_id(pdf) not in tracker]
    if not new_pdfs:
        print(
            f"\n[신규 없음] 목표주가 미처리 PDF 0건 "
            f"(총 {len(pdfs)}개 처리 완료)"
        )
        print("  확인 후: python main.py analyst --targets --merge")
        return

    if test:
        new_pdfs = new_pdfs[:1]
        print(f"\n[목표주가 테스트 모드] 1개만 처리: {new_pdfs[0].name}")
    else:
        print(f"\n목표주가 미처리 리포트 {len(new_pdfs)}건 처리 시작...")

    client = None
    saved_count = 0
    for pdf in new_pdfs:
        # Filename problems are hard rejects and need no API call.
        try:
            _validate_target_filename(pdf)
        except TargetValidationError as exc:
            reason = str(exc)
            logger.warning(f"  {pdf.name} 거부: {reason}")
            _record_target_status(tracker, pdf, "rejected", reason)
            continue

        try:
            if client is None:
                client = _gemini_client()
            raw = _extract_target_from_pdf(client, pdf)
        except Exception as exc:
            # API/transport failures remain untracked so a later run retries them.
            logger.error(f"  {pdf.name} Gemini/API 실패(재시도 가능): {exc}")
            continue

        try:
            payload = _parse_target_response(raw)
            row = _build_target_row(pdf, payload)
        except (TargetValidationError, ValueError, TypeError) as exc:
            reason = str(exc)
            logger.warning(f"  {pdf.name} 거부: {reason}")
            _record_target_status(tracker, pdf, "rejected", reason)
            continue

        if row is None:
            _record_target_status(tracker, pdf, "no_target")
            logger.info(f"  {pdf.name}: 목표주가 없음")
            continue

        saved_count = _append_target_staged(row)
        _record_target_status(tracker, pdf, "saved", row["review_reason"])
        logger.info(
            f"  {pdf.name}: 목표주가 {row['target_price_krw']:,}원 저장"
        )

    if saved_count:
        print(
            f"\n  → data/analyst_targets_staged.csv 에 "
            f"총 {saved_count}건 저장 완료"
        )
        print("  PDF 수동 검증 후: python main.py analyst --targets --merge")
    else:
        print("\n  신규 목표주가 저장 행 없음")


def _merge_target_staged():
    """Merge staged rows into analyst_targets.csv, keeping the latest file id."""
    import pandas as pd

    if not _TARGETS_STAGED_CSV.exists():
        print(
            "\n[병합할 목표주가 파일 없음] 먼저 "
            "python main.py analyst --targets 를 실행하세요."
        )
        return

    staged = _read_csv_with_fallback(_TARGETS_STAGED_CSV)
    if _TARGETS_CSV.exists():
        existing = _read_csv_with_fallback(_TARGETS_CSV)
        combined = pd.concat([existing, staged], ignore_index=True)
    else:
        combined = staged

    combined = (
        combined.reindex(columns=_TARGET_COLUMNS)
        .fillna("")
        .drop_duplicates(subset=["source_file_id"], keep="last")
        .sort_values(["report_date", "broker", "source_file"])
        .reset_index(drop=True)
    )
    _TARGETS_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(_TARGETS_CSV, index=False, encoding="utf-8-sig")
    _TARGETS_STAGED_CSV.unlink()
    print(f"\n  → analyst_targets.csv 업데이트 완료: 총 {len(combined)}건")


if __name__ == '__main__':
    import sys

    flags = set(sys.argv[1:])
    process_target_reports(
        merge=('-' * 2 + 'merge') in flags,
        test=('-' * 2 + 'test') in flags,
    )


__all__ = [
    "TargetValidationError",
    "process_target_reports",
    "_TARGETS_CSV",
    "_TARGETS_STAGED_CSV",
    "_TARGETS_TRACKER",
    "_TARGET_COLUMNS",
    "_TARGET_RESPONSE_SCHEMA",
    "_extract_target_from_pdf",
    "_parse_target_response",
    "_korean_price_candidates",
    "_evidence_contains_price",
    "_validate_target_payload",
    "_target_direction",
    "_build_target_row",
]
