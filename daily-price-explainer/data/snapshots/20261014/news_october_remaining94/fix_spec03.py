import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=HERE/'specs_03.json'
s=json.loads(p.read_text(encoding='utf-8'))
for a in s['articles']:
    if a.get('availability')=='metadata_only':
        a.update(event_type='other',event_stage='unknown',mechanisms=['unclear'],impact_horizon='unknown',forward_looking=False)
s['notes']['20231103']+=' 뉴시스 메타는 수정 시각 12:37:29이므로 최초 등록 11:33:04 유지.'
s['notes']['20231206']+=' 한국정경신문 RFC 형식 메타에는 GMT 표기가 있으나 화면 승인 10:42 KST 기준; 시각 메타 불일치 표시.'
s['notes']['20231227']+=' 뉴시스 수정 메타 07:25:31 대신 화면 최초 등록 06:00 사용.'
p.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
