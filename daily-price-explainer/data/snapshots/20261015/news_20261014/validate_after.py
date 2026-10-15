"""Verify hashes, byte prefixes, schema, attribution and completed-day coverage."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
def sha(raw):
    return hashlib.sha256(raw).hexdigest()
def read_rows(raw):
    return [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
def url_key(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace('www3.', '').removeprefix('www.').removeprefix('m.')
    query = parse_qs(parsed.query)
    if host == 'newsis.com':
        return (host, query.get('id', [parsed.path.rstrip('/').split('/')[-1]])[0])
    if 'mk.co.kr' in host:
        return ('mk.co.kr', parsed.path.rstrip('/').split('/')[-1])
    if host == 'newstomato.com':
        return (host, query.get('no', [parsed.path])[0])
    return (host, parsed.path.rstrip('/'), tuple((key, tuple(value)) for key, value in sorted(query.items()) if key not in ['utm_source', 'utm_medium', 'utm_campaign', 'outputType', 'mid', 'reply', 'ref', 'source']))

manifest = json.loads((HERE / 'append_manifest.json').read_text(encoding='utf-8'))
initial = json.loads((HERE / 'initial_manifest.json').read_text(encoding='utf-8'))
drafts = {}
all_rows = {}
checks = {}
for name, kind in [('news_property_codex_batch.jsonl', 'articles'), ('news_search_log_codex.jsonl', 'logs')]:
    raw = (ROOT / 'data' / name).read_bytes()
    report = manifest['files'][name]
    draft = (HERE / f'{kind}.jsonl').read_bytes()
    assert sha(raw) == report['after_sha256']
    assert sha(raw[:report['original_prefix_bytes']]) == report['before_sha256'] == initial['files'][name]['sha256']
    assert sha(draft) == report['batch_sha256']
    assert raw[report['original_prefix_bytes']:] == draft
    rows = read_rows(raw)
    assert len(rows) == report['after_rows']
    assert rows[report['before_rows']:] == read_rows(draft)
    all_rows[kind] = rows
    drafts[kind] = read_rows(draft)
    checks[name] = {'sha256': sha(raw), 'rows': len(rows), 'original_prefix_unchanged': True, 'exact_suffix_matches_artifact': True}

validator = Draft202012Validator(json.loads((ROOT / 'schemas/news_property.schema.json').read_text(encoding='utf-8')), format_checker=FormatChecker())
existing_schema_issues = []
for index, row in enumerate(all_rows['articles'], start=1):
    errors = list(validator.iter_errors(row))
    if index <= manifest['files']['news_property_codex_batch.jsonl']['before_rows']:
        for error in errors:
            existing_schema_issues.append({'line': index, 'trading_date': row.get('trading_date'), 'url': row.get('url'), 'path': '.'.join(str(part) for part in error.path), 'message': error.message})
    else:
        assert not errors, errors
for row in drafts['articles']:
    assert row['trading_date'] == '20261014'
    assert datetime.fromisoformat(initial['window_kst'][0]) < datetime.fromisoformat(row['published_at']) <= datetime.fromisoformat(initial['window_kst'][1])
    assert row['extraction']['prompt_version'] == 'news_gap_20261014.v1'
    assert row['extraction']['review_status'] == 'unreviewed'
    assert row['article_id'] == sha((row['url'] + '|' + (row['article_text_hash'] or '')).encode('utf-8'))
    if row['article_text_hash']:
        assert len(row['article_text_hash']) == 64
    if row['event_type'] == 'market_flow':
        assert row['sentiment_score'] == row['ex_ante_impact_direction'] == 0
    if row['published_at'].startswith('2026-10-13'):
        assert row['event_time_bucket'] == 'post_close'

old = all_rows['articles'][:-len(drafts['articles'])]
old_keys = {url_key(row['url']) for row in old}
new_keys = [url_key(row['url']) for row in drafts['articles']]
assert len(new_keys) == len(set(new_keys))
assert not set(new_keys) & old_keys
assert len({r['article_id'] for r in all_rows['articles']}) == len(all_rows['articles'])
assert len({r['trading_date'] for r in all_rows['logs']}) == len(all_rows['logs'])
log = drafts['logs'][0]
assert len(drafts['logs']) == 1 and log['trading_date'] == '20261014' and log['sampling_role'] == ''
assert log['articles_found'] == len(drafts['articles']) and log['status'] == 'completed_with_articles'
calendar = json.loads((ROOT / 'data/snapshots/20261014/news_october_remaining94/trading_calendar_dates.json').read_text(encoding='utf-8'))
assert max(calendar) >= '20261013'
expected_dates = {d for d in calendar if '20211130' <= d <= '20261013'} | {'20261014'}
actual_dates = {row['trading_date'] for row in all_rows['logs']}
assert expected_dates == actual_dates, {'missing': sorted(expected_dates - actual_dates), 'extra': sorted(actual_dates - expected_dates)}
result = {'new_batch_passed': True, 'all_article_schema_passed': not existing_schema_issues,
          'files': checks, 'new_article_schema_rows_passed': len(drafts['articles']),
          'existing_schema_issue_count': len(existing_schema_issues), 'existing_schema_issues': existing_schema_issues,
          'added_articles': len(drafts['articles']), 'added_logs': 1, 'first_trading_date': min(actual_dates),
          'last_trading_date': max(actual_dates), 'missing_trading_dates': [],
          'calendar_reference': 'Previously captured trading calendar through 20261013, plus the user-provided 20261014 target',
          'new_canonical_url_duplicates': 0, 'all_article_id_duplicates': 0,
          'availability': dict(Counter(r['availability'] for r in drafts['articles'])),
          'time_buckets': dict(Counter(r['event_time_bucket'] for r in drafts['articles'])),
          'body_hash_available': sum(bool(r['article_text_hash']) for r in drafts['articles']),
          'market_flow_neutral_rows': sum(r['event_type'] == 'market_flow' for r in drafts['articles'])}
(HERE / 'validation_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))

lines = ['2026-10-14 뉴스 배치 완료: 1/1, 기사 13건·검색 로그 1건.',
         '아래 JSONL은 이미 누적 파일 두 곳에 append되어 있습니다. 중복 append 없이 검증 후 검토·커밋하십시오.',
         'prompt_version = news_gap_20261014.v1; review_status = unreviewed.',
         f"신규 13행 스키마 통과. 기존 데이터 스키마 문제 {len(existing_schema_issues)}건은 원본 그대로 보존했으며 validation_summary.json에 별도 기록했습니다.", '',
         '| 파일 | append 전 SHA-256 | append 후 SHA-256 |', '|---|---|---|']
for name, info in manifest['files'].items():
    lines.append(f"| {name} | `{info['before_sha256']}` | `{info['after_sha256']}` |")
for kind, name in [('articles', 'news_property_codex_batch.jsonl'), ('logs', 'news_search_log_codex.jsonl')]:
    lines += ['', f'{name}에 추가된 줄:', '', '```jsonl', (HERE / f'{kind}.jsonl').read_text(encoding='utf-8').rstrip('\n'), '```']
(HERE / 'OUTPUT_FOR_CLAUDE.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
