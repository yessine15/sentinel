"""Pytest configuration — stub mode for agent tests.

All agent unit tests default to ``RUN_MODE=stub`` so they don't
require a live cluster.  Integration tests (files named
``test_live_*.py``) override this to ``live``.
"""

from __future__ import annotations

import os


def pytest_configure(config):
    """Set RUN_MODE=stub before any test module is imported."""
    os.environ.setdefault("RUN_MODE", "stub")
