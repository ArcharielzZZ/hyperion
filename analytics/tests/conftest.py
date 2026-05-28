"""Pytest path setup for analytics tests."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO_ROOT / "analytics" / "dashboard"

for path in (REPO_ROOT, DASHBOARD_DIR):
    s = str(path)
    if s not in sys.path:
        sys.path.insert(0, s)
