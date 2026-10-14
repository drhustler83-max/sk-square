import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'specs_05.json'
s=json.loads(p.read_text(encoding='utf-8'))
s['keep_visible_times']=[u for u in s['keep_visible_times'] if any(x in u for x in ('newsis.com','mk.co.kr'))]
s['notes']['20250116']+=' 키움 분석 기사에는 2025/11/10 수정 메타가 있음; 처음 공개된 날짜로 귀속하고 본문의 당시 분기 전망만 사용. 본문 해시는 현재 확보본 해시이며 최초 발행본 보증은 아님.'
s['notes']['20250314']+=' 뉴시스 메타 16:32:24는 수정 시각이어서 화면 최초 등록 15:43:43을 사용.'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
