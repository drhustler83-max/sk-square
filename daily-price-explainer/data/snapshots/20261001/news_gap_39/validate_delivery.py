"""Verify append-only delivery, schema, calendar attribution and copy-ready output."""
import csv, hashlib, json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator, FormatChecker
from build_batch import atomic
HERE=Path(__file__).resolve().parent
BASE=HERE.parents[3]
def sha(b):return hashlib.sha256(b).hexdigest()
def rows(b):return [json.loads(v) for v in b.decode('utf-8-sig').splitlines() if v.strip()]
targets=[r['date'] for r in csv.DictReader((BASE/'data/news_search_targets_gap_20260804_20260930.csv').open(encoding='utf-8-sig'))]
validator=Draft202012Validator(json.loads((BASE/'schemas/news_property.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker())
manifests=[json.loads((HERE/f'batch{i:02}_manifest.json').read_text(encoding='utf-8')) for i in (1,2)]
articles=rows((HERE/'batch01_articles.jsonl').read_bytes())+rows((HERE/'batch02_articles.jsonl').read_bytes())
logs=rows((HERE/'batch01_logs.jsonl').read_bytes())+rows((HERE/'batch02_logs.jsonl').read_bytes())
counts=Counter(r['trading_date'] for r in articles)
assert [r['trading_date'] for r in logs]==targets
assert len(logs)==len(targets)==39
assert len({r['url'] for r in articles})==len(articles)
assert len({r['article_id'] for r in articles})==len(articles)
for r in articles:
    validator.validate(r)
    assert r['extraction']['prompt_version']=='news_gap_39.v1'
    assert r['extraction']['review_status']=='unreviewed'
    assert r['article_id']==sha((r['url']+'|'+(r['article_text_hash'] or '')).encode('utf-8'))
    assert r['evidence_facts'] and len(r['evidence_facts'])<=5
    assert all('\ufffd' not in v for v in r['entities']+r['evidence_facts'])
    if r['event_type']=='market_flow':
        assert r['sentiment_score']==0 and r['ex_ante_impact_direction']==0
    pub=datetime.fromisoformat(r['published_at']).astimezone(ZoneInfo('Asia/Seoul'))
    date=pub.strftime('%Y%m%d')
    if date not in targets: bucket='non_trading_day'; date=next(d for d in targets if d>date)
    elif pub.strftime('%H:%M:%S')>'15:30:00':bucket='post_close';date=next(d for d in targets if d>date)
    else:bucket='pre_open' if pub.hour<9 else 'intraday'
    assert r['trading_date']==date and r['event_time_bucket']==bucket
for r in logs:
    assert set(r)=={'trading_date','sampling_role','status','articles_found','notes'}
    assert r['sampling_role']=='' and r['articles_found']==counts[r['trading_date']]
    assert r['status']==('completed_with_articles' if r['articles_found'] else 'completed_no_news')
files={}
for name,kind in [('news_property_codex_batch.jsonl','articles'),('news_search_log_codex.jsonl','logs')]:
    data=(BASE/'data'/name).read_bytes()
    first=manifests[0]['files'][name];last=manifests[1]['files'][name]
    assert sha(data)==last['after_sha256']
    assert sha(data[:first['prefix_bytes_unchanged']])==first['before_sha256']
    assert sha(data[:last['prefix_bytes_unchanged']])==last['before_sha256']==first['after_sha256']
    batch1=(HERE/f'batch01_{kind}.jsonl').read_bytes();batch2=(HERE/f'batch02_{kind}.jsonl').read_bytes()
    assert data[first['prefix_bytes_unchanged']:]==batch1+batch2
    assert sha(batch1)==first['batch_sha256'] and sha(batch2)==last['batch_sha256']
    old=rows(data[:first['prefix_bytes_unchanged']]);new=articles if kind=='articles' else logs
    key='url' if kind=='articles' else 'trading_date'
    assert not {r[key] for r in old}&{r[key] for r in new}
    files[name]={'sha256':sha(data),'original_prefix_sha256':first['before_sha256'],'original_prefix_bytes':first['prefix_bytes_unchanged'],'added_rows':len(new),'total_rows':len(rows(data))}
summary={'status':'passed','completed_dates':39,'articles_added':len(articles),'logs_added':len(logs),'schema_valid':len(articles),'new_url_duplicates':0,'new_id_duplicates':0,'attribution_errors':0,'original_bytes_unchanged':True,'availability':dict(Counter(r['availability'] for r in articles)),'event_types':dict(Counter(r['event_type'] for r in articles)),'market_flow_neutral_checked':sum(r['event_type']=='market_flow' for r in articles),'per_date':dict(counts),'files':files,'review_status':'unreviewed','search_scope':'general web results plus adaptive portfolio searches; not exhaustive article enumeration'}
atomic(HERE/'validation_summary.json',(json.dumps(summary,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
parts=['# Claude Code 검증용 뉴스 39일 결과\n\n진행: 39/39. 기사 57줄, 검색 로그 39줄. 아래 JSONL은 지정된 두 파일에 이미 append되어 있습니다. 같은 작업폴더에서 검증할 때 다시 append하지 말고 URL/article_id 중복 및 현재 파일 SHA-256을 확인하세요. 다른 사본에 병합할 때만 아래 추가분을 사용하세요.\n\n모든 레코드는 unreviewed, prompt_version=news_gap_39.v1입니다. 일반 웹 검색 범위에서 확인한 기사를 수록했으며 모든 기사의 망라를 뜻하지 않습니다. schema·중복·귀속·로그 기사 수·기존 바이트 보존 검증을 통과했습니다.\n']
for i,n in [(1,20),(2,39)]:
    for kind in ('articles','logs'):
        parts.append(f'\n## 배치 {i}: {n}/39 — {kind}\n\n```jsonl\n'+(HERE/f'batch{i:02}_{kind}.jsonl').read_text(encoding='utf-8')+'```\n')
parts.append('\n검증 해시:\n\n```json\n'+json.dumps(files,ensure_ascii=False,indent=2)+'\n```\n')
atomic(HERE/'OUTPUT_FOR_CLAUDE.md',''.join(parts).encode('utf-8'))
print(json.dumps(summary,ensure_ascii=False,indent=2))
