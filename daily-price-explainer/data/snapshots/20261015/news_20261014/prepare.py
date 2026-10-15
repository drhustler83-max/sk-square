"""Snapshot input metadata before researching and appending this one-day batch."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FILES = ["news_property_codex_batch.jsonl", "news_search_log_codex.jsonl"]
def sha(value):
    return hashlib.sha256(value).hexdigest()

if __name__ == "__main__":
    manifest = {"created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
                "target": "20261014", "window_kst": ["2026-10-13T15:30:00+09:00", "2026-10-14T15:30:00+09:00"],
                "git_head": subprocess.check_output(["git", "-C", str(ROOT.parent), "rev-parse", "HEAD"], text=True).strip(),
                "files": {}}
    for name in FILES:
        raw = (ROOT / "data" / name).read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        assert not any(row["trading_date"] == "20261014" for row in rows), name
        manifest["files"][name] = {"sha256": sha(raw), "bytes": len(raw), "rows": len(rows), "max_trading_date": max(row["trading_date"] for row in rows)}
    path = HERE / "initial_manifest.json"
    assert not path.exists(), "Do not overwrite the initial snapshot"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
