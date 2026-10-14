import csv, hashlib, json
from collections import Counter
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from build_batch import attribute
HERE=Path(__file__).resolve().parent
BASE=HERE.parents[3]
initial=json.loads((HERE/'initial_manifest.json').read_text(encoding='utf-8'))
all_rows={}
new_rows={}
for name,info in initial.items():
    data=(BASE/'data'/name).read_bytes()
    assert hashlib.sha256(data[:info['bytes']]).hexdigest()==info['sha256']
    all_rows[name]=[json.loads(x) for x in data.decode('utf-8-sig').splitlines() if x.strip()]
    new_rows[name]=[json.loads(x) for x in data[info['bytes']:].decode('utf-8').splitlines() if x.strip()]
articles=all_rows['news_property_codex_batch.jsonl']
logs=all_rows['news_search_log_codex.jsonl']
new_articles=new_rows['news_property_codex_batch.jsonl']
new_logs=new_rows['news_search_log_codex.jsonl']
validator=Draft202012Validator(json.loads((BASE/'schemas/news_property.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker())
for a in new_articles:
    validator.validate(a)
    assert attribute(a['published_at'])==(a['trading_date'],a['event_time_bucket'])
    assert a['extraction']['review_status']=='unreviewed'
    assert a['article_id']==hashlib.sha256((a['url']+'|'+(a['article_text_hash'] or '')).encode('utf-8')).hexdigest()
    if a['event_type']=='market_flow':assert a['sentiment_score']==a['ex_ante_impact_direction']==0
counts=Counter(x['trading_date'] for x in logs)
groups={}
for name in ('news_search_targets_gap_202610.csv','news_search_targets_gap_294_remaining94.csv'):
    target=list(csv.DictReader((BASE/'data'/name).open(encoding='utf-8-sig')))
    missing=[r['date'] for r in target if counts[r['date']]!=1]
    assert not missing
    groups[name]={'target_dates':len(target),'exactly_one_log_per_date':True,'missing_or_duplicated':missing}
calendar=json.loads((HERE/'trading_calendar_dates.json').read_text(encoding='utf-8'))
last=max(counts)
missing_calendar=[d for d in calendar if d<=last and d not in counts]
within_logged_range=[d for d in missing_calendar if d>=min(counts)]
report={'status':'passed','target_coverage':groups,'all_log_rows':len(logs),'unique_logged_dates':len(counts),'log_min_date':min(counts),'log_max_date':last,'duplicate_log_dates':{d:n for d,n in counts.items() if n>1},'calendar_dates_to_last_log':sum(d<=last for d in calendar),'calendar_missing_dates':missing_calendar,'gaps_within_logged_range':within_logged_range,'new_article_rows':len(new_articles),'new_log_rows':len(new_logs),'new_availability':dict(Counter(a['availability'] for a in new_articles)),'new_event_types':dict(Counter(a['event_type'] for a in new_articles)),'new_articles_with_text_hash':sum(a['article_text_hash'] is not None for a in new_articles),'original_prefixes_unchanged':True,'all_new_rows_unreviewed':True,'market_flow_direction_and_sentiment_neutral':True}
(HERE/'quality_coverage_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
