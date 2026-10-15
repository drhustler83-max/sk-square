"""Remove only verified temporary validation copies and print exact appended JSONL."""
import hashlib
import json
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / 'append_manifest.json').read_text(encoding='utf-8'))
for name, kind in [('news_property_codex_batch.jsonl', 'articles'), ('news_search_log_codex.jsonl', 'logs')]:
    temporary_copy = (HERE / f'validated_copy_{kind}.jsonl').resolve()
    assert temporary_copy.parent == HERE
    if temporary_copy.exists():
        assert hashlib.sha256(temporary_copy.read_bytes()).hexdigest() == manifest['files'][name]['after_sha256']
        temporary_copy.unlink()
part = int(sys.argv[1]) if len(sys.argv) > 1 else 0
articles = (HERE / 'articles.jsonl').read_text(encoding='utf-8').splitlines()
if part == 1:
    print('\n'.join(articles[:7]))
elif part == 2:
    print('\n'.join(articles[7:]))
    print((HERE / 'logs.jsonl').read_text(encoding='utf-8'), end='')
else:
    print('Verified temporary copies removed; exact append artifacts and manifests retained.')
