"""skeleton (서브모듈, cd9c13e 고정 — OPT.Dec9) 을 import 경로에 넣는다. skeleton 코드는 고치지 않는다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKELETON = ROOT / "skeleton"
if str(SKELETON) not in sys.path:
    sys.path.insert(0, str(SKELETON))
