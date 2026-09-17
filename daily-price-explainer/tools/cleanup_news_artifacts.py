"""낡은 뉴스 작업 아티팩트 2건 정리.

① data/news_search_remaining.csv — "아직 검색 안 한 날" 목록인데 10일 모두
   news_search_log_codex.jsonl 에 completed_ 로 기록돼 있다. 그런데
   experiment_residual_news.py 가 이 목록을 무조건 미완료로 덮어써서
   (search_complete = False) 매칭 표본이 그만큼 깎이고 있었다.
   → 로그와 대조해 실제 완료된 날을 제거한다. (PROJECT_MAP §6-⑤)

② data/news_property_queue.jsonl — status="pending" 39건.
   모두 구 표본(event_residual_biased.csv) 기준으로 만들어진 대기열이다.
   news_property_codex_batch.jsonl 에 이미 추출된 건은 completed 로,
   현행 매칭 표본(event_residual_unbiased.csv)에 없어 앞으로도 추출되지 않을
   건은 superseded 로 확정한다.

원본은 *.bak 으로 백업한다.

Usage:
  python tools/cleanup_news_artifacts.py --dry-run
  python tools/cleanup_news_artifacts.py
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
D = BASE / "data"
SEARCH_LOG = D / "news_search_log_codex.jsonl"
REMAINING = D / "news_search_remaining.csv"
QUEUE = D / "news_property_queue.jsonl"
PROPERTIES = D / "news_property_codex_batch.jsonl"
UNBIASED = D / "event_residual_unbiased.csv"

STAMP = datetime.now().isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _backup(path: Path, dry: bool) -> None:
    if dry or not path.exists():
        return
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"    백업 → {bak.name}")


def fix_remaining(dry: bool) -> dict:
    print(f"\n{'='*88}")
    print("  ① news_search_remaining.csv")
    print(f"{'='*88}")

    log = _read_jsonl(SEARCH_LOG)
    status: dict[str, str] = {}
    for r in log:
        key = str(r.get("trading_date", "")).zfill(8)
        val = str(r.get("status", ""))
        # 완료 기록이 하나라도 있으면 완료로 본다
        if val.startswith("completed_") or key not in status:
            status[key] = val
    print(f"  검색 로그 {len(log)}줄 / 고유 거래일 {len(status)}일")
    print(f"  상태 분포: {dict(Counter(status.values()).most_common())}")

    if not REMAINING.exists():
        print("  파일 없음 — 건너뜀")
        return {"skipped": True}

    rem = pd.read_csv(REMAINING, dtype=str)
    rem["date"] = rem["date"].str.zfill(8)
    print(f"\n  대기 목록 {len(rem)}일 — 로그 대조")
    print(f"    {'날짜':<10} {'역할':<22} {'로그 상태':<26} 판정")
    resolved, still = [], []
    for _, row in rem.iterrows():
        d = row["date"]
        st = status.get(d, "(로그 없음)")
        done = st.startswith("completed_")
        (resolved if done else still).append(d)
        print(f"    {d:<10} {str(row.get('sampling_role','')):<22} {st:<26} "
              f"{'완료 → 제거' if done else '미완료 → 유지'}")

    print(f"\n  완료 {len(resolved)}일 / 실제 미완료 {len(still)}일")

    if not dry:
        _backup(REMAINING, dry)
        out = rem.loc[rem["date"].isin(still)]
        out.to_csv(REMAINING, index=False, encoding="utf-8-sig")
        print(f"    갱신 → {REMAINING.name} ({len(out)}행, 헤더 유지)")

    return {
        "file": REMAINING.name,
        "rows_before": int(len(rem)),
        "resolved_completed": resolved,
        "still_pending": still,
        "rows_after": int(len(still)),
    }


def fix_queue(dry: bool) -> dict:
    print(f"\n{'='*88}")
    print("  ② news_property_queue.jsonl")
    print(f"{'='*88}")

    queue = _read_jsonl(QUEUE)
    if not queue:
        print("  파일 없음 / 비어 있음 — 건너뜀")
        return {"skipped": True}
    print(f"  대기열 {len(queue)}건")
    print(f"  현재 상태: {dict(Counter(str(r.get('status')) for r in queue).most_common())}")

    props = _read_jsonl(PROPERTIES)
    extracted_ids = {str(r.get("article_id")) for r in props if r.get("article_id")}
    extracted_urls = {str(r.get("url")) for r in props if r.get("url")}
    print(f"  추출 완료본 {len(props)}건 (고유 article_id {len(extracted_ids)})")

    sample_dates: set[str] = set()
    if UNBIASED.exists():
        ev = pd.read_csv(UNBIASED, dtype=str)
        sample_dates = set(ev["date"].astype(str).str.zfill(8))
    print(f"  현행 매칭 표본 거래일 {len(sample_dates)}일")

    tally = Counter()
    for r in queue:
        if str(r.get("status")) != "pending":
            tally["이미 확정"] += 1
            continue
        aid, url = str(r.get("article_id")), str(r.get("url"))
        td = str(r.get("trading_date", "")).zfill(8)
        if aid in extracted_ids or url in extracted_urls:
            r["status"] = "completed"
            r["resolution_note"] = "news_property_codex_batch.jsonl 에 추출본 존재"
            tally["completed"] += 1
        elif sample_dates and td not in sample_dates:
            r["status"] = "superseded"
            r["resolution_note"] = (
                "구 표본(event_residual_biased) 전용 항목. "
                "현행 매칭 표본에 없는 거래일이라 추출 대상 아님")
            tally["superseded"] += 1
        else:
            r["status"] = "superseded"
            r["resolution_note"] = (
                "구 표본 대기열. 해당 거래일은 매칭 표본에 있으나 "
                "이 기사(article_id)는 추출본에 없음 — 재수집 대상 아님")
            tally["superseded(표본내)"] += 1
        r["resolved_at"] = STAMP

    print(f"\n  판정: {dict(tally.most_common())}")
    for r in queue:
        if r.get("resolved_at") == STAMP:
            print(f"    {r['trading_date']}  {r['status']:<12} {str(r.get('publisher'))[:28]}")

    if not dry:
        _backup(QUEUE, dry)
        _write_jsonl(QUEUE, queue)
        print(f"    갱신 → {QUEUE.name} ({len(queue)}건)")

    return {
        "file": QUEUE.name,
        "records": int(len(queue)),
        "resolution": dict(tally),
        "pending_after": sum(1 for r in queue if r.get("status") == "pending"),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.dry_run:
        print("[dry-run] 파일을 쓰지 않습니다.")

    r1 = fix_remaining(a.dry_run)
    r2 = fix_queue(a.dry_run)

    print(f"\n{'='*88}")
    print("  요약")
    print(f"{'='*88}")
    if not r1.get("skipped"):
        print(f"  remaining.csv  {r1['rows_before']}행 → {r1['rows_after']}행 "
              f"(완료 확인 {len(r1['resolved_completed'])}일 제거)")
    if not r2.get("skipped"):
        print(f"  queue.jsonl    pending {r2['records']}건 → 잔여 pending {r2['pending_after']}건")
    print("\n  다음 단계: python -m tools.experiment_residual_news 재실행 → 표본 증가 확인")


if __name__ == "__main__":
    main()
