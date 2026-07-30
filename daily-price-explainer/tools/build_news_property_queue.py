"""
Build an article-only extraction queue from event_residual_biased.csv.

Only third-party news URLs are included. DART and official SK domains are
excluded. Price, divergence, high_residual, direction, importance, note, and
event_name are intentionally omitted so the extractor cannot see outcome or
human ex-post labels.

Usage:
  python tools/build_news_property_queue.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse


BASE = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = BASE / "data" / "event_residual_biased.csv"
DEFAULT_OUTPUT = BASE / "data" / "news_property_queue.jsonl"
URL_PATTERN = re.compile(r"https?://[^|,\s]+")


def _canonical_url(value: str) -> str:
    return value.rstrip(").]}>\"'")


def _is_official(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    host = host.removeprefix("www.")
    return (
        host == "dart.fss.or.kr"
        or host == "sksquare.com"
        or host.endswith(".sksquare.com")
        or host == "sk.com"
        or host.endswith(".sk.com")
    )


def build_queue(input_path: Path, output_path: Path) -> list[dict]:
    queue: list[dict] = []
    seen: set[str] = set()

    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        for csv_row, row in enumerate(csv.DictReader(handle), start=2):
            for raw_url in URL_PATTERN.findall(row.get("source", "") or ""):
                url = _canonical_url(raw_url)
                if _is_official(url) or url in seen:
                    continue
                seen.add(url)
                digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
                queue.append(
                    {
                        "queue_version": "news_property_queue.v1",
                        "article_id": f"sha256:{digest}",
                        "trading_date": str(row.get("date", "")),
                        "url": url,
                        "publisher": urlparse(url).netloc.lower().removeprefix("www."),
                        "source_csv": input_path.name,
                        "source_csv_row": csv_row,
                        "extractor_input_policy": "article_only_no_market_or_human_labels",
                        "status": "pending",
                    }
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in queue:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return queue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    queue = build_queue(args.input, args.output)
    print(f"Queued {len(queue)} third-party news URLs: {args.output}")


if __name__ == "__main__":
    main()
