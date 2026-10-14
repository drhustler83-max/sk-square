"""Review manually specified properties, validate, then atomically append completed dates."""
import csv,hashlib,json,os,tempfile,argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator,FormatChecker
BASE=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
def sha(value):return hashlib.sha256(value).hexdigest()
def atomic(path,payload,expected=None):
    if expected is not None and path.read_bytes()!=expected:raise ValueError(f'Concurrent change: {path}')
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as h:h.write(payload);h.flush();os.fsync(h.fileno())
        if expected is not None and path.read_bytes()!=expected:raise ValueError(f'Concurrent change: {path}')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
def read_rows(payload):return [json.loads(s) for s in payload.decode('utf-8-sig').splitlines() if s.strip()]
def attribute(published):
    pub=datetime.fromisoformat(published).astimezone(ZoneInfo('Asia/Seoul'));d=pub.strftime('%Y%m%d')
    calendar=json.loads((HERE/'trading_calendar_dates.json').read_text(encoding='utf-8'))
    if d not in calendar:return next(t for t in calendar if t>d),'non_trading_day'
    if pub.strftime('%H:%M:%S')>'15:30:00':return next(t for t in calendar if t>d),'post_close'
    return d,'pre_open' if pub.hour<9 else 'intraday'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('batch',type=int,choices=[0,1,2,3,4,5]);args=parser.parse_args()
    specs=json.loads((HERE/f'specs_{args.batch:02}.json').read_text(encoding='utf-8'))
    group='october' if args.batch==0 else 'remaining94'
    target_name='news_search_targets_gap_202610.csv' if args.batch==0 else 'news_search_targets_gap_294_remaining94.csv'
    prompt_version='news_gap_202610.v1' if args.batch==0 else 'news_gap_294_remaining.v1'
    target_rows=list(csv.DictReader((BASE/'data'/target_name).open(encoding='utf-8-sig')))
    start=0 if args.batch==0 else (args.batch-1)*20
    batch_days=[r['date'] for r in (target_rows if args.batch==0 else target_rows[start:start+20])]
    days=specs['completed_days'];assert days and days==batch_days[:len(days)]
    role={r['date']:r['sampling_role'] for r in target_rows}
    now=datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    validator=Draft202012Validator(json.loads((BASE/'schemas/news_property.schema.json').read_text(encoding='utf-8')),format_checker=FormatChecker())
    articles=[]
    for spec in specs['articles']:
        row={'schema_version':'news_property.v1','article_id':'','trading_date':'','url':spec['url'],'publisher':spec['publisher'],'title':spec['title'],'published_at':spec['published_at'],'event_time_bucket':'unknown','retrieved_at':now,'article_text_hash':None,'availability':'partial_text','language':'ko','primary_subject':'sk_hynix','entities':['SK하이닉스'],'relevance_score':.5,'event_type':'market_flow','event_stage':'follow_up','novelty_score':.1,'sentiment_score':0,'surprise_direction':'unknown','surprise_magnitude':0,'materiality_score':.15,'uncertainty_score':.3,'forward_looking':False,'ex_ante_impact_direction':0,'impact_horizon':'intraday','mechanisms':['supply_demand','sector_beta'],'confidence_score':.9,'numeric_claims':[],'evidence_facts':[],'duplicate_cluster_id':None,'extraction':{'extractor':'codex','model':'gpt-6','prompt_version':prompt_version,'extracted_at':now,'review_status':'unreviewed'}}
        row.update(spec);row['trading_date'],row['event_time_bucket']=attribute(row['published_at'])
        assert row['trading_date'] in days,row['url']
        row['article_id']=sha((row['url']+'|'+(row['article_text_hash'] or '')).encode('utf-8'))
        validator.validate(row)
        if row['event_type']=='market_flow':assert row['sentiment_score']==row['ex_ante_impact_direction']==0
        assert row['evidence_facts'] and all('\ufffd' not in s for s in row['entities']+row['evidence_facts'])
        articles.append(row)
    articles.sort(key=lambda r:(r['trading_date'],r['published_at'],r['url']))
    assert len({r['url'] for r in articles})==len(articles)
    counts=Counter(r['trading_date'] for r in articles)
    logs=[{'trading_date':d,'sampling_role':role[d],'status':'completed_with_articles' if counts[d] else 'completed_no_news','articles_found':counts[d],'notes':specs['notes'][d]} for d in days]
    pending=[];report={'batch':args.batch,'group':group,'progress':start+len(days),'target_total':len(target_rows),'completed_days':days,'article_rows':len(articles),'log_rows':len(logs),'at':now,'files':{}}
    for name,kind,rows in [('news_property_codex_batch.jsonl','articles',articles),('news_search_log_codex.jsonl','logs',logs)]:
        path=BASE/'data'/name;before=path.read_bytes();old=read_rows(before)
        key='url' if kind=='articles' else 'trading_date'
        assert not {r.get(key) for r in old}&{r[key] for r in rows}
        if kind=='articles':assert not {r.get('article_id') for r in old}&{r['article_id'] for r in rows}
        payload=('\n'.join(json.dumps(r,ensure_ascii=False,separators=(',',':')) for r in rows)+'\n').encode('utf-8') if rows else b''
        after=before+(b'\n' if before and not before.endswith(b'\n') else b'')+payload
        artifact=HERE/f'batch{args.batch:02}_{kind}.jsonl';assert not artifact.exists()
        atomic(artifact,payload);pending.append((path,before,after))
        report['files'][name]={'before_sha256':sha(before),'after_sha256':sha(after),'original_prefix_bytes':len(before),'batch_sha256':sha(payload),'added_rows':len(rows)}
    for path,before,after in pending:atomic(path,after,expected=before);assert path.read_bytes()==after
    atomic(HERE/f'batch{args.batch:02}_manifest.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
