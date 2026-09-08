from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def clean_scalar(value):
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


print("=== RELATED TABULAR FILES ===")
for rel in [
    "data/event_residual_unbiased.csv",
    "data/event_residual_biased.csv",
    "data/factor_log.csv",
    "data/news_search_remaining.csv",
]:
    path = ROOT / rel
    print(f"\n--- {rel} EXISTS={path.exists()} SIZE={path.stat().st_size if path.exists() else None} ---")
    if not path.exists():
        continue
    df = pd.read_csv(path)
    print(f"ROWS={len(df)} COLS={len(df.columns)}")
    print("COLUMNS=" + json.dumps([str(c) for c in df.columns], ensure_ascii=False))
    print("DTYPES=" + json.dumps({str(c): str(df[c].dtype) for c in df.columns}, ensure_ascii=False))
    miss = df.isna().sum()
    print("MISSING=" + json.dumps({str(c): int(miss[c]) for c in df.columns if int(miss[c])}, ensure_ascii=False))
    head = []
    for record in df.head(2).to_dict(orient="records"):
        head.append({str(k): clean_scalar(v) for k, v in record.items()})
    print("HEAD2=" + json.dumps(head, ensure_ascii=False))


for rel in ["data/news_property_codex_batch.jsonl", "data/news_search_log_codex.jsonl"]:
    path = ROOT / rel
    print(f"\n--- {rel} EXISTS={path.exists()} SIZE={path.stat().st_size if path.exists() else None} ---")
    if not path.exists():
        continue
    rows = []
    keys = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            rows.append(obj)
            keys.update(obj.keys())
    print(f"ROWS={len(rows)} TOP_LEVEL_KEYS=" + json.dumps(sorted(keys), ensure_ascii=False))
    print("HEAD2=" + json.dumps(rows[:2], ensure_ascii=False, default=str))
