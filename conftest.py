"""pytest config: makes semantic-library's modules importable (same flat-
import convention the production code uses — the directory name has a
hyphen, so it can't be a dotted package)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "semantic-library"))
