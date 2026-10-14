import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'specs_02.json'
s=json.loads(p.read_text(encoding='utf-8'))
s['keep_visible_times'].append('https://dealsite.co.kr/articles/110569')
s['keep_visible_times']=[u for u in s['keep_visible_times'] if 'biz.chosun.com' not in u]
s['notes']['20230818']+=' 검색 화면 입력은 14:57이나 직접 페이지 최초 발행 메타는 14:03:37 KST; 메타를 사용하고 둘 다 장중으로 귀속.'
s['notes']['20230822']+=' 뉴시스 메타는 수정 12:08:06이므로 화면 최초 등록 11:27:33을 사용.'
s['notes']['20230926']+=' 일반 페이지 메타는 9/27 07:35:13이나 본문에 명시된 유료 서비스 최초 공개 9/26 08:43:34를 우선.'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
