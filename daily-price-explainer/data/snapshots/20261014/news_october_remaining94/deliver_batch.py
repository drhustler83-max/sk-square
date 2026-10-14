"""Validate actual prefixes and batch artifacts, then render exact JSONL output."""
import csv,json,sys,io
from collections import Counter
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from build_batch import atomic,sha,read_rows,attribute
HERE=Path(__file__).resolve().parent;BASE=HERE.parents[3]
batch=int(sys.argv[1]);initial=json.loads((HERE/'initial_manifest.json').read_text(encoding='utf-8'))
manifests=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(HERE.glob('batch??_manifest.json'))]
validator=Draft202012Validator(json.loads((BASE/'schemas/news_property.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker())
new={};files={}
for name,kind in [('news_property_codex_batch.jsonl','articles'),('news_search_log_codex.jsonl','logs')]:
    data=(BASE/'data'/name).read_bytes();prefix=initial[name]
    assert sha(data[:prefix['bytes']])==prefix['sha256']
    artifacts=[(HERE/f"batch{m['batch']:02}_{kind}.jsonl").read_bytes() for m in manifests]
    assert data[prefix['bytes']:]==b''.join(artifacts)
    assert sha(data)==manifests[-1]['files'][name]['after_sha256']
    previous=prefix['sha256']
    for m,payload in zip(manifests,artifacts):
        assert m['files'][name]['before_sha256']==previous
        assert m['files'][name]['batch_sha256']==sha(payload)
        previous=m['files'][name]['after_sha256']
    new[kind]=read_rows(b''.join(artifacts))
    files[name]={'initial_sha256':prefix['sha256'],'current_sha256':sha(data),'original_prefix_bytes':prefix['bytes'],'added_rows':len(new[kind]),'total_rows':len(read_rows(data))}
articles=new['articles'];logs=new['logs'];counts=Counter(r['trading_date'] for r in articles)
assert len({r['url'] for r in articles})==len(articles)==len({r['article_id'] for r in articles})
assert len({r['trading_date'] for r in logs})==len(logs)
roles={}
for name in ['news_search_targets_gap_202610.csv','news_search_targets_gap_294_remaining94.csv']:
    for r in csv.DictReader((BASE/'data'/name).open(encoding='utf-8-sig')):roles[r['date']]=r['sampling_role']
for r in articles:
    validator.validate(r);assert r['extraction']['review_status']=='unreviewed'
    assert r['article_id']==sha((r['url']+'|'+(r['article_text_hash'] or '')).encode('utf-8'))
    assert (r['trading_date'],r['event_time_bucket'])==attribute(r['published_at'])
    assert r['evidence_facts'] and all('\ufffd' not in s for s in r['entities']+r['evidence_facts']+[r['title']])
    assert r['extraction']['prompt_version']==('news_gap_202610.v1' if r['trading_date'].startswith('202610') else 'news_gap_294_remaining.v1')
    if r['event_type']=='market_flow':assert r['sentiment_score']==r['ex_ante_impact_direction']==0
for r in logs:
    assert set(r)=={'trading_date','sampling_role','status','articles_found','notes'}
    assert r['sampling_role']==roles[r['trading_date']] and r['articles_found']==counts[r['trading_date']]
    assert r['status']==('completed_with_articles' if counts[r['trading_date']] else 'completed_no_news')
october=sum(r['trading_date'].startswith('202610') for r in logs);remaining=len(logs)-october
report={'status':'passed','october_completed':october,'october_total':7,'remaining94_completed':remaining,'remaining94_total':94,'new_articles':len(articles),'new_logs':len(logs),'schema_validated':len(articles),'duplicate_new_url_or_id':0,'attribution_errors':0,'original_bytes_unchanged':True,'files':files}
atomic(HERE/'validation_summary.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
parts=['# Claude Code 검증용 추가분\n\n아래 JSONL은 지정된 파일에 이미 append했습니다. 같은 작업폴더에서는 중복 append하지 마세요. 모두 unreviewed이며 일반 웹 검색에서 확인한 기사만 수록한 결과입니다.\n']
for m in manifests:
    i=m['batch'];label=f"{m['group']} {m['progress']}/{m['target_total']}"
    block=[f'\n## 배치 {i} — {label}\n\n전후 SHA-256:\n\n```json\n'+json.dumps(m['files'],ensure_ascii=False,indent=2)+'\n```\n']
    for kind in ['articles','logs']:block.append(f'\n### {kind}\n\n```jsonl\n'+(HERE/f'batch{i:02}_{kind}.jsonl').read_text(encoding='utf-8')+'```\n')
    content=''.join(block);parts.append(content);atomic(HERE/f'BATCH{i:02}_FOR_CLAUDE.md',content.encode('utf-8'))
atomic(HERE/'OUTPUT_FOR_CLAUDE.md',''.join(parts).encode('utf-8'))
pending=[r for r in csv.DictReader((BASE/'data/news_search_targets_gap_294_remaining94.csv').open(encoding='utf-8-sig')) if r['date'] not in {x['trading_date'] for x in logs}]
stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['date','sampling_role','reason'],lineterminator='\n');writer.writeheader();writer.writerows(pending)
atomic(HERE/'remaining_pending.csv',stream.getvalue().encode('utf-8'))
print(json.dumps(report,ensure_ascii=False,indent=2))
