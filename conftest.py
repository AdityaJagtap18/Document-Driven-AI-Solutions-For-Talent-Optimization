"""pytest config: makes semantic-library's, context-lake's, and
azure-functions' modules importable (same flat-import convention the
production code uses — the directory names have hyphens, so they can't be
dotted packages)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "semantic-library"))
sys.path.insert(0, str(ROOT / "context-lake"))
sys.path.insert(0, str(ROOT / "azure-functions"))
sys.path.insert(0, str(ROOT / "voicebot"))
