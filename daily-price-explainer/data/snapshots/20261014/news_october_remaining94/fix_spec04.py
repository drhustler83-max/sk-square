import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'specs_04.json'
s=json.loads(p.read_text(encoding='utf-8'))
s['keep_visible_times']=[u for u in s['keep_visible_times'] if any(x in u for x in ('newsis.com','hankyung.com','mk.co.kr'))]
s['notes']['20240129']+=' 뉴시스 메타는 수정 13:05:29이므로 화면 최초 등록 11:45:51 사용.'
s['notes']['20240509']+=' 조선일보 화면 09:18, 직접 발행 메타 09:17:25 KST의 차이는 메타를 사용; 둘 다 장중.'
s['notes']['20240805']+=' 뉴시스 수정 메타 15:26:03 대신 최초 등록 15:22:32 사용.'
s['notes']['20240807']+=' 뉴시스 메타 19:34:51은 수정 시각이며 최초 등록은 16:02:52.'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
