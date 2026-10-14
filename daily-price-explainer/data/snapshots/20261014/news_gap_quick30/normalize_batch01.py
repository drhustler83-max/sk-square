import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
HERE=Path(__file__).resolve().parent
path=HERE/'specs_01.json';specs=json.loads(path.read_text(encoding='utf-8'))
receipts={r['url']:r for r in json.loads((HERE/'receipts_01.json').read_text(encoding='utf-8'))}
excluded=[]
for spec in list(specs['articles']):
    if 'smarttoday.co.kr' in spec['url']:
        excluded.append({'url':spec['url'],'reason':'원래 검색·HTML 메타 09:51:12와 현재 이동된 페이지 화면 18:51이 달라 거래일 귀속 불명. 추정하지 않고 제외.'})
        specs['articles'].remove(spec);continue
    for meta in receipts[spec['url']].get('date_metadata',[]):
        if meta.get('property')=='article:published_time':
            spec['published_at']=datetime.fromisoformat(meta['content'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).isoformat();break
    if spec['url'] in ['https://stock.mk.co.kr/news/view/706795','https://stock.mk.co.kr/news/view/725188','https://www.inews24.com/view/1867774']:
        spec['availability']='full_text'
    if spec['url']=='https://stock.mk.co.kr/news/view/725188':
        spec['event_stage']='announcement'
        spec['evidence_facts']=['티맵이 서울공항리무진 지분 100% 매각을 위한 주식매매계약을 체결했다고 업계 취재를 전한다.','공항버스 거래의 계약 체결과 향후 추가 계열사 정리 계획을 구분하며 클로징 완료로 해석하지 않는다.']
specs['notes']['20250327']+=' 스마트투데이 자사주 기사는 발행 메타(09:51:12)와 이동된 페이지 화면(18:51)이 달라 귀속 불명으로 제외; 주총 투자재원 기사는 시각 확인 후 수록.'
path.write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(HERE/'excluded_sources_01.json').write_text(json.dumps(excluded,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
