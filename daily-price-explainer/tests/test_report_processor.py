"""Network-free regression tests for analyst report processors."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.report_processor import _parse_json_response
from tools.target_report_processor import (
    TargetValidationError,
    _TARGET_RESPONSE_SCHEMA,
    _build_target_row,
    _evidence_contains_price,
    _extract_target_from_pdf,
    _parse_target_response,
    _target_direction,
    _validate_target_payload,
)


def _payload(**overrides):
    payload = {
        "target_price_krw": 97_000,
        "prev_target_price_krw": 87_000,
        "investment_opinion": "매수",
        "evidence": "목표주가 97,000원 (기존 87,000원), 투자의견 매수",
        "extraction_note": None,
    }
    payload.update(overrides)
    return payload


class TargetResponseTests(unittest.TestCase):
    def test_fenced_json_parser_and_validator_success(self):
        raw = "```json\n" + json.dumps(_payload(), ensure_ascii=False) + "\n```"
        parsed = _parse_target_response(raw)
        validated = _validate_target_payload(parsed)

        self.assertEqual(validated["target_price_krw"], 97_000)
        self.assertEqual(validated["prev_target_price_krw"], 87_000)
        self.assertEqual(validated["investment_opinion"], "매수")

    def test_korean_evidence_number_normalization(self):
        examples = (
            "목표주가 97,000원",
            "목표주가 9.7만원",
            "목표주가 9만7000원",
            "목표주가 9만 7천원",
        )
        for evidence in examples:
            with self.subTest(evidence=evidence):
                self.assertTrue(_evidence_contains_price(97_000, evidence))

    def test_current_null_is_valid_no_target_and_builds_no_row(self):
        no_target = _payload(
            target_price_krw=None,
            prev_target_price_krw=None,
            investment_opinion=None,
            evidence=None,
            extraction_note=None,
        )
        self.assertIsNone(_validate_target_payload(no_target)["target_price_krw"])

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "BNK투자증권_SK스퀘어_리포트_20240502.pdf"
            pdf.write_bytes(b"%PDF-test")
            self.assertIsNone(_build_target_row(pdf, no_target))

    def test_bool_range_and_evidence_failures_are_rejected(self):
        invalid_payloads = (
            _payload(target_price_krw=True),
            _payload(prev_target_price_krw=False),
            _payload(target_price_krw=999),
            _payload(target_price_krw=10_000_001),
            _payload(evidence="목표주가 96,000원 (기존 87,000원)"),
            _payload(evidence="목표주가 97,000원"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(TargetValidationError):
                    _validate_target_payload(payload)

    def test_direction_four_cases(self):
        self.assertEqual(_target_direction(97_000, 87_000), "up")
        self.assertEqual(_target_direction(87_000, 97_000), "down")
        self.assertEqual(_target_direction(97_000, 97_000), "unchanged")
        self.assertEqual(_target_direction(97_000, None), "unknown")

    def test_build_row_uses_filename_and_ignores_model_direction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "BNK투자증권_SK스퀘어_리포트_20240502.pdf"
            pdf.write_bytes(b"%PDF-test")
            payload = _payload(
                direction="down",
                extraction_note="table OCR uncertain",
            )

            row = _build_target_row(pdf, payload)

        self.assertEqual(row["report_date"], "20240502")
        self.assertEqual(row["broker"], "BNK투자증권")
        self.assertEqual(row["target_price_krw"], 97_000)
        self.assertEqual(row["prev_target_price_krw"], 87_000)
        self.assertEqual(row["direction"], "up")
        self.assertEqual(row["change_pct"], 11.4943)
        self.assertEqual(row["extraction_method"], "gemini_structured_v1")
        self.assertTrue(row["source_file_id"].startswith(row["source_file"] + ":"))

    def test_every_saved_gemini_row_requires_pdf_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "SK증권_SK스퀘어_리포트_20240503.pdf"
            pdf.write_bytes(b"%PDF-test")

            plain_row = _build_target_row(pdf, _payload(extraction_note=None))
            noted_row = _build_target_row(
                pdf,
                _payload(extraction_note="first-page table blurred"),
            )

        fixed_reason = "Gemini-only extraction; PDF verification required"
        self.assertIs(plain_row["needs_review"], True)
        self.assertEqual(plain_row["review_reason"], fixed_reason)
        self.assertIs(noted_row["needs_review"], True)
        self.assertEqual(
            noted_row["review_reason"],
            fixed_reason + "; extraction_note: first-page table blurred",
        )

    def test_response_schema_requires_all_fields_and_no_direction(self):
        self.assertIsInstance(_TARGET_RESPONSE_SCHEMA, dict)
        self.assertEqual(
            set(_TARGET_RESPONSE_SCHEMA["required"]),
            {
                "target_price_krw",
                "prev_target_price_krw",
                "investment_opinion",
                "evidence",
                "extraction_note",
            },
        )
        self.assertNotIn("direction", _TARGET_RESPONSE_SCHEMA["properties"])


class _FakeFiles:
    def __init__(self):
        self.upload_calls = []
        self.delete_calls = []

    def upload(self, **kwargs):
        self.upload_calls.append(kwargs)
        return SimpleNamespace(name="files/fake-report", state="ACTIVE")

    def get(self, **kwargs):  # pragma: no cover - ACTIVE skips polling
        raise AssertionError(f"unexpected polling call: {kwargs}")

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class _FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(_payload(), ensure_ascii=False))


class StructuredGeminiCallTests(unittest.TestCase):
    def test_one_generate_call_json_config_and_uploaded_file_cleanup(self):
        files = _FakeFiles()
        models = _FakeModels()
        client = SimpleNamespace(files=files, models=models)

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "BNK투자증권_SK스퀘어_리포트_20240502.pdf"
            pdf.write_bytes(b"%PDF-test")
            raw = _extract_target_from_pdf(client, pdf)

        self.assertEqual(len(models.calls), 1)
        call = models.calls[0]
        self.assertEqual(call["model"], "gemini-2.5-flash")
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertIsInstance(call["config"]["response_schema"], dict)
        self.assertEqual(len(files.upload_calls), 1)
        self.assertEqual(files.delete_calls, [{"name": "files/fake-report"}])
        self.assertEqual(_parse_target_response(raw)["target_price_krw"], 97_000)


class LegacyEventParserRegressionTests(unittest.TestCase):
    def test_legacy_free_form_event_array_parser_is_unchanged(self):
        raw = """```json
        [{
          "date": "20240502",
          "category": "증권사리포트",
          "event": "목표주가 상향",
          "direction": "+",
          "importance": "M",
          "note": "기존 목표가 대비 상향",
          "source": "BNK투자증권 리포트 20240502"
        }]
        ```"""

        rows = _parse_json_response(raw)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "20240502")
        self.assertEqual(rows[0]["event"], "목표주가 상향")
        self.assertEqual(rows[0]["direction"], "+")


if __name__ == "__main__":
    unittest.main()
