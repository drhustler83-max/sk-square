import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'specs_01.json'
s=json.loads(p.read_text(encoding='utf-8'))
for a in s['articles']:
    if 'www.emetro.co.kr' in a['url']:
        a['url']=a['url'].replace('www.emetro.co.kr','api.emetro.co.kr')
s['keep_visible_times']=['https://www.newsis.com/view/NISX20230726_0002390989']
s['notes']['20230726']+=' 뉴시스 메타 published_time은 수정 시각 12:42:05를 가리켜 화면 최초 등록 11:10:40을 사용.'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
