"""Validate both append-only batches and produce exact, copy-ready JSONL blocks."""
import csv,io,json
from collections import Counter
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from build_batch import sha,atomic,read_rows,attribute
HERE=Path(__file__).resolve().parent;BASE=HERE.parents[3]
initial=json.loads((HERE/'initial_manifest.json').read_text(encoding='utf-8'))
manifests=[json.loads((HERE/f'batch{i:02}_manifest.json').read_text(encoding='utf-8')) for i in (1,2)]
target_rows=list(csv.DictReader((BASE/'data/news_search_targets_gap_294_quick30.csv').open(encoding='utf-8-sig')))
targets=[r['date'] for r in target_rows]
articles=read_rows((HERE/'batch01_articles.jsonl').read_bytes())+read_rows((HERE/'batch02_articles.jsonl').read_bytes())
logs=read_rows((HERE/'batch01_logs.jsonl').read_bytes())+read_rows((HERE/'batch02_logs.jsonl').read_bytes())
validator=Draft202012Validator(json.loads((BASE/'schemas/news_property.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker())
assert len(targets)==len(logs)==30 and [r['trading_date'] for r in logs]==targets
assert len({r['url'] for r in articles})==len(articles)==len({r['article_id'] for r in articles})
counts=Counter(r['trading_date'] for r in articles)
for r in articles:
    validator.validate(r)
    assert r['extraction']['prompt_version']=='news_gap_294_remaining.v1' and r['extraction']['review_status']=='unreviewed'
    assert r['article_id']==sha((r['url']+'|'+(r['article_text_hash'] or '')).encode('utf-8'))
    assert (r['trading_date'],r['event_time_bucket'])==attribute(r['published_at'])
    assert r['evidence_facts'] and all('\ufffd' not in v for v in r['entities']+r['evidence_facts']+[r['title']])
    if r['event_type']=='market_flow':assert r['sentiment_score']==r['ex_ante_impact_direction']==0
for r in logs:
    assert set(r)=={'trading_date','sampling_role','status','articles_found','notes'}
    assert r['sampling_role']=='unused_low' and r['articles_found']==counts[r['trading_date']]
    assert r['status']==('completed_with_articles' if r['articles_found'] else 'completed_no_news')
files={}
for name,kind in [('news_property_codex_batch.jsonl','articles'),('news_search_log_codex.jsonl','logs')]:
    data=(BASE/'data'/name).read_bytes();origin=initial[name]
    assert sha(data[:origin['bytes']])==origin['sha256']
    assert sha(data)==manifests[-1]['files'][name]['after_sha256']
    first=(HERE/f'batch01_{kind}.jsonl').read_bytes();second=(HERE/f'batch02_{kind}.jsonl').read_bytes()
    assert data[origin['bytes']:]==first+second
    assert manifests[0]['files'][name]['before_sha256']==origin['sha256']
    assert manifests[1]['files'][name]['before_sha256']==manifests[0]['files'][name]['after_sha256']
    for m,payload in zip(manifests,[first,second]):assert m['files'][name]['batch_sha256']==sha(payload)
    old=read_rows(data[:origin['bytes']]);new=articles if kind=='articles' else logs
    key='url' if kind=='articles' else 'trading_date'
    assert not {r.get(key) for r in old}&{r[key] for r in new}
    files[name]={'before_sha256':origin['sha256'],'after_sha256':sha(data),'original_prefix_bytes':origin['bytes'],'added_rows':len(new),'total_rows':len(read_rows(data))}
remaining=list(csv.DictReader((BASE/'data/news_search_targets_gap_294_remaining.csv').open(encoding='utf-8-sig')))
next_rows=[r for r in remaining if r['date'] not in set(targets)]
assert len(remaining)==124 and len(next_rows)==94
stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=['date','sampling_role','reason'],lineterminator='\n');writer.writeheader();writer.writerows(next_rows)
atomic(HERE/'remaining_after_quick30.csv',stream.getvalue().encode('utf-8'))
report={'status':'passed','completed':30,'target_count':30,'articles_added':len(articles),'logs_added':len(logs),'new_url_duplicates':0,'new_id_duplicates':0,'attribution_errors':0,'original_bytes_unchanged':True,'uncompleted_target_dates':[],'next_remaining_days':94,'schema_rows_validated':len(articles),'market_flow_neutral_checked':sum(r['event_type']=='market_flow' for r in articles),'availability':dict(Counter(r['availability'] for r in articles)),'per_date':dict(counts),'files':files,'review_status':'unreviewed','started_at':'2026-10-14T14:29:07+09:00','last_append_at':manifests[-1]['at']}
atomic(HERE/'validation_summary.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
blocks=[]
for i,n in [(1,20),(2,30)]:
    parts=[f'# Batch {i}: {n}/30 completed\n\nArticles and logs below are ALREADY APPENDED in the local workspace. Do not append again when reviewing these same files. All extraction records are unreviewed, prompt_version=news_gap_294_remaining.v1.\n']
    for kind in ('articles','logs'):parts.append(f'\n## {kind}\n\n```jsonl\n'+(HERE/f'batch{i:02}_{kind}.jsonl').read_text(encoding='utf-8')+'```\n')
    text=''.join(parts);blocks.append(text);atomic(HERE/f'BATCH{i:02}_FOR_CLAUDE.md',text.encode('utf-8'))
parts=['# 뉴스 quick30 검증용 추가분\n\n30/30 완료. 기사 39줄, 검색 로그 30줄. 두 JSONL에 이미 append했으므로 같은 작업폴더에서 중복 append하지 마세요. 다른 사본에 병합할 때만 아래 추가분을 사용하세요.\n\n원래 124일 중 나머지 94일은 검색 로그에 추가하지 않았고 대상 CSV도 수정하지 않았습니다. 다음 작업용 94일 목록은 remaining_after_quick30.csv에 있습니다. 일반 웹에서 확인한 검색 결과를 수록한 것이며 기사 전수 망라를 뜻하지 않습니다.\n\n'+ '\n'.join(blocks),'\n## 검증 해시\n\n```json\n'+json.dumps(files,ensure_ascii=False,indent=2)+'\n```\n']
atomic(HERE/'OUTPUT_FOR_CLAUDE.md',''.join(parts).encode('utf-8'))
print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__!='__main__':raise RuntimeError('Run as a script')
