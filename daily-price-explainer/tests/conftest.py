"""테스트가 프로젝트 루트를 import 경로로 잡게 한다.

tests/ 안의 파일이 `from tools.x import y` 같은 절대 import를 쓰므로,
러너가 tests/ 를 sys.path[0] 로 잡아도 루트가 보이도록 여기서 넣어준다.
(이 파일이 없으면 tests/ 하위에서 tools·memory·prompts import가 깨진다)
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
