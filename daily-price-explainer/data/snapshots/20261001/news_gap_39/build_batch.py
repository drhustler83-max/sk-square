"""Validate manually reviewed article-only properties and preserve JSONL prefixes."""
import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

BASE = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, payload, expected=None):
    if expected is not None and path.read_bytes() != expected:
        raise ValueError(f"Concurrent change: {path}")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if expected is not None and path.read_bytes() != expected:
            raise ValueError(f"Concurrent change: {path}")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=int, choices=[1, 2])
    args = parser.parse_args()
    specs = json.loads((HERE / f"specs_{args.batch:02}.json").read_text(encoding="utf-8"))
    targets = [r["date"] for r in csv.DictReader((BASE / "data/news_search_targets_gap_20260804_20260930.csv").open(encoding="utf-8-sig"))]
    days = targets[:20] if args.batch == 1 else targets[20:]
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    validator = Draft202012Validator(json.loads((BASE / "schemas/news_property.schema.json").read_text(encoding="utf-8")), format_checker=FormatChecker())
    articles = []
    for spec in specs["articles"]:
        row = {
            "schema_version": "news_property.v1", "article_id": "",
            "trading_date": "", "url": spec["url"], "publisher": spec["publisher"],
            "published_at": spec["published_at"], "event_time_bucket": "unknown",
            "retrieved_at": now, "article_text_hash": None,
            "availability": "partial_text", "language": "ko", "title": spec["title"],
            "primary_subject": "sk_hynix", "entities": ["SK하이닉스"],
            "relevance_score": 0.6, "event_type": "market_flow", "event_stage": "follow_up",
            "novelty_score": 0.1, "sentiment_score": 0, "surprise_direction": "unknown",
            "surprise_magnitude": 0, "materiality_score": 0.15, "uncertainty_score": 0.3,
            "forward_looking": False, "ex_ante_impact_direction": 0, "impact_horizon": "intraday",
            "mechanisms": ["supply_demand", "sector_beta"], "confidence_score": 0.85,
            "numeric_claims": [], "evidence_facts": spec["evidence_facts"],
            "duplicate_cluster_id": None,
            "extraction": {"extractor": "codex", "model": "gpt-6", "prompt_version": "news_gap_39.v1", "extracted_at": now, "review_status": "unreviewed"},
        }
        row.update(spec)
        pub = datetime.fromisoformat(row["published_at"]).astimezone(ZoneInfo("Asia/Seoul"))
        date = pub.strftime("%Y%m%d")
        if date not in targets:
            row["event_time_bucket"] = "non_trading_day"
            candidates = [d for d in targets if d > date]
        elif pub.strftime("%H:%M:%S") > "15:30:00":
            row["event_time_bucket"] = "post_close"
            candidates = [d for d in targets if d > date]
        else:
            row["event_time_bucket"] = "pre_open" if pub.hour < 9 else "intraday"
            candidates = [date]
        if not candidates:
            raise ValueError(f"Outside target attribution: {row['url']}")
        row["trading_date"] = candidates[0]
        assert row["trading_date"] in days, row["url"]
        row["article_id"] = digest((row["url"] + "|" + (row["article_text_hash"] or "")).encode("utf-8"))
        validator.validate(row)
        articles.append(row)
    articles.sort(key=lambda r: (r["trading_date"], r["published_at"], r["url"]))
    assert len({r["url"] for r in articles}) == len(articles)
    counts = Counter(r["trading_date"] for r in articles)
    logs = [{"trading_date": d, "sampling_role": "", "status": "completed_with_articles" if counts[d] else "completed_no_news", "articles_found": counts[d], "notes": specs["notes"].get(d, "일반 웹 검색 및 발행시간 확인; 동일 사건 중복·비기사 페이지 제외. 시황 가격 결과로 방향을 정하지 않음." if counts[d] else "일반 웹 검색 범위에서 귀속·관련성이 확인된 기사 없음. 뉴스의 부재를 확정하는 기록은 아님.")} for d in days]
    paths = [("news_property_codex_batch.jsonl", articles, "articles"), ("news_search_log_codex.jsonl", logs, "logs")]
    pending = []
    report = {"batch": args.batch, "dates": days, "articles": len(articles), "log_rows": len(logs), "completed": 20 if args.batch == 1 else 39, "at": now, "files": {}, "id_recipe": "SHA256(canonical URL + '|' + (article_text_hash or ''))"}
    for name, rows, kind in paths:
        path = BASE / "data" / name
        before = path.read_bytes()
        old = [json.loads(line) for line in before.decode("utf-8-sig").splitlines() if line.strip()]
        if kind == "logs":
            assert not set(days) & {r["trading_date"] for r in old}
        else:
            assert not {r["url"] for r in rows} & {r.get("url") for r in old}
            assert not {r["article_id"] for r in rows} & {r.get("article_id") for r in old}
        payload = ("\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n").encode("utf-8")
        if not rows:
            payload = b""
        after = before + (b"\n" if before and not before.endswith(b"\n") else b"") + payload
        assert after.startswith(before)
        artifact = HERE / f"batch{args.batch:02}_{kind}.jsonl"
        assert not artifact.exists(), artifact
        atomic(artifact, payload)
        pending.append((path, before, after))
        report["files"][name] = {"before_sha256": digest(before), "after_sha256": digest(after), "prefix_bytes_unchanged": len(before), "added_rows": len(rows), "batch_sha256": digest(payload)}
    for path, before, after in pending:
        atomic(path, after, expected=before)
        assert path.read_bytes() == after
    atomic(HERE / f"batch{args.batch:02}_manifest.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
