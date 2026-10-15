"""Validate separate JSONL drafts, then atomically append the completed day only."""
import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
KST = timezone(timedelta(hours=9))
DAY = '20261014'
VERSION = 'news_gap_20261014.v1'
def sha(value):
    return hashlib.sha256(value).hexdigest()
def read_rows(raw):
    return [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
def atomic(path, payload, expected=None):
    if expected is not None and path.read_bytes() != expected:
        raise ValueError(f'Concurrent modification: {path}')
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if expected is not None and path.read_bytes() != expected:
            raise ValueError(f'Concurrent modification: {path}')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
def serialized(rows):
    return ('\n'.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows) + '\n').encode('utf-8')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--append', action='store_true')
    args = parser.parse_args()
    initial = json.loads((HERE / 'initial_manifest.json').read_text(encoding='utf-8'))
    assert not (HERE / 'append_manifest.json').exists(), 'Batch already appended; use validate_after.py'
    specs = json.loads((HERE / 'specs.json').read_text(encoding='utf-8'))
    targets = list(csv.DictReader((ROOT / 'data/news_search_targets_gap_20261014.csv').open(encoding='utf-8-sig')))
    assert targets == [{'date': DAY, 'sampling_role': '', 'reason': 'after_log_end'}]
    assert specs['completed_days'] == [DAY]
    schema = json.loads((ROOT / 'schemas/news_property.schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    now = datetime.now(KST).isoformat(timespec='seconds')
    lower = datetime.fromisoformat(initial['window_kst'][0])
    upper = datetime.fromisoformat(initial['window_kst'][1])
    articles = []
    for spec in specs['articles']:
        row = {'schema_version': 'news_property.v1', 'article_id': '', 'trading_date': DAY,
               'published_at': None, 'event_time_bucket': 'unknown', 'retrieved_at': now,
               'article_text_hash': None, 'availability': 'metadata_only', 'language': 'ko',
               'surprise_direction': 'unknown', 'surprise_magnitude': 0, 'numeric_claims': [],
               'duplicate_cluster_id': None,
               'extraction': {'extractor': 'codex', 'model': 'gpt-6', 'prompt_version': VERSION,
                              'extracted_at': now, 'review_status': 'unreviewed'}}
        row.update(spec)
        published = datetime.fromisoformat(row['published_at']).astimezone(KST)
        assert lower < published <= upper, row['url']
        row['event_time_bucket'] = 'post_close' if published.date() < upper.date() else ('pre_open' if published.hour < 9 else 'intraday')
        row['article_id'] = sha((row['url'] + '|' + (row['article_text_hash'] or '')).encode('utf-8'))
        validator.validate(row)
        assert row['evidence_facts'] and all('\ufffd' not in s for s in row['evidence_facts'] + row['entities'])
        if row['event_type'] == 'market_flow':
            assert row['sentiment_score'] == row['ex_ante_impact_direction'] == 0
        articles.append(row)
    articles.sort(key=lambda row: (row['published_at'], row['url']))
    logs = [{'trading_date': DAY, 'sampling_role': targets[0]['sampling_role'],
             'status': 'completed_with_articles' if articles else 'completed_no_news',
             'articles_found': len(articles), 'notes': specs['notes'][DAY]}]
    payloads = {'news_property_codex_batch.jsonl': ('articles', articles),
                'news_search_log_codex.jsonl': ('logs', logs)}
    pending = []
    report = {'target': DAY, 'progress': '1/1', 'at': now, 'prompt_version': VERSION,
              'article_rows': len(articles), 'log_rows': 1,
              'event_time_bucket': dict(Counter(r['event_time_bucket'] for r in articles)),
              'availability': dict(Counter(r['availability'] for r in articles)),
              'market_flow_zero_direction_rows': sum(r['event_type'] == 'market_flow' for r in articles),
              'files': {}}
    for name, (kind, rows) in payloads.items():
        path = ROOT / 'data' / name
        before = path.read_bytes()
        old = read_rows(before)
        assert sha(before) == initial['files'][name]['sha256'], f'Initial hash changed: {name}'
        assert len(before) == initial['files'][name]['bytes']
        key = 'url' if kind == 'articles' else 'trading_date'
        assert len({r[key] for r in rows}) == len(rows)
        assert not {r.get(key) for r in old} & {r[key] for r in rows}, f'Duplicate {key}'
        if kind == 'articles':
            assert not {r.get('article_id') for r in old} & {r['article_id'] for r in rows}
            assert len({r['article_id'] for r in rows}) == len(rows)
        payload = serialized(rows)
        after = before + (b'\n' if before and not before.endswith(b'\n') else b'') + payload
        assert read_rows(after)[len(old):] == rows
        atomic(HERE / f'{kind}.jsonl', payload)
        atomic(HERE / f'validated_copy_{kind}.jsonl', after)
        assert read_rows((HERE / f'validated_copy_{kind}.jsonl').read_bytes()) == old + rows
        report['files'][name] = {'before_sha256': sha(before), 'after_sha256': sha(after),
                                 'original_prefix_bytes': len(before), 'batch_sha256': sha(payload),
                                 'before_rows': len(old), 'added_rows': len(rows), 'after_rows': len(old) + len(rows)}
        pending.append((path, before, after))
    assert all(path.read_bytes() == before for path, before, after in pending)
    if args.append:
        for path, before, after in pending:
            atomic(path, after, expected=before)
            assert path.read_bytes() == after
        atomic(HERE / 'append_manifest.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    else:
        atomic(HERE / 'draft_validation.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(json.dumps({'appended': args.append, **report}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
