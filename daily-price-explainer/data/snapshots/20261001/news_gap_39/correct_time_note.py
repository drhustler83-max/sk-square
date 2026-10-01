"""Correct a wording error in the newly appended time-source note only."""
import json
from pathlib import Path
from build_batch import atomic,digest
HERE=Path(__file__).resolve().parent
BASE=HERE.parents[3]
batch=HERE/'batch02_logs.jsonl'
before_batch=batch.read_bytes()
old='조선비즈 최초 발행 HTML 메타(UTC)를 KST로 변환; 화면 수정시각과 차이가 있으나 모두 개장 전.'
new='조선비즈 발행 HTML 메타(UTC)를 KST로 변환(08:47:54.812); 화면 입력 표기(08:50)와 차이가 있으나 모두 개장 전. 공개시각 차이는 후속 검증 대상.'
assert before_batch.count(old.encode('utf-8'))==1
after_batch=before_batch.replace(old.encode('utf-8'),new.encode('utf-8'))
path=BASE/'data/news_search_log_codex.jsonl'
before=path.read_bytes()
assert before.endswith(before_batch)
after=before[:-len(before_batch)]+after_batch
atomic(path,after,expected=before)
atomic(batch,after_batch,expected=before_batch)
manifest_path=HERE/'batch02_manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
manifest['files'][path.name]['after_sha256']=digest(after)
manifest['files'][path.name]['batch_sha256']=digest(after_batch)
atomic(manifest_path,(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
spec_path=HERE/'specs_02.json'
spec=json.loads(spec_path.read_text(encoding='utf-8'))
spec['notes']['20260930']=spec['notes']['20260930'].replace(old,new)
atomic(spec_path,(json.dumps(spec,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
atomic(HERE/'note_correction_manifest.json',(json.dumps({'reason':'화면 입력 표기를 수정시각으로 부른 notes 문구 수정. 기사 속성은 변경 없음.','file':path.name,'before_sha256':digest(before),'after_sha256':digest(after),'batch_before_sha256':digest(before_batch),'batch_after_sha256':digest(after_batch)},ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
