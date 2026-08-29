"""Make the ``agents.device`` package importable when collecting these tests.

The Device agent lives in a plain code directory (no ``pyproject.toml``), so
pytest needs the repository root on ``sys.path`` for the ``agents`` namespace
package to resolve. This mirrors how the Agent Lab CLI will run team agents.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
