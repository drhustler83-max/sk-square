from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "output" / "event_residual_unbiased_with_news_properties.xlsx"


def clean_scalar(value):
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


print(f"BOOK={BOOK}")
print(f"EXISTS={BOOK.exists()} SIZE={BOOK.stat().st_size if BOOK.exists() else None}")
book = pd.ExcelFile(BOOK)
print("SHEETS=" + json.dumps(book.sheet_names, ensure_ascii=False))

for sheet in book.sheet_names:
    df = pd.read_excel(BOOK, sheet_name=sheet)
    print(f"\n=== SHEET {sheet!r} ROWS={len(df)} COLS={len(df.columns)} ===")
    print("COLUMNS=" + json.dumps([str(c) for c in df.columns], ensure_ascii=False))
    print("DTYPES=" + json.dumps({str(c): str(df[c].dtype) for c in df.columns}, ensure_ascii=False))
    miss = df.isna().sum()
    print("MISSING=" + json.dumps({str(c): int(miss[c]) for c in df.columns if int(miss[c])}, ensure_ascii=False))
    nunique = df.nunique(dropna=True)
    print("NUNIQUE=" + json.dumps({str(c): int(nunique[c]) for c in df.columns}, ensure_ascii=False))
    for c in df.columns:
        name = str(c).lower()
        if any(k in name for k in ("date", "time", "날짜", "일자")):
            vals = pd.to_datetime(df[c], errors="coerce")
            if vals.notna().any():
                print(f"DATE_RANGE {c!r}: {vals.min()} .. {vals.max()} valid={vals.notna().sum()}")
    head = []
    for record in df.head(3).to_dict(orient="records"):
        head.append({str(k): clean_scalar(v) for k, v in record.items()})
    print("HEAD3=" + json.dumps(head, ensure_ascii=False))
