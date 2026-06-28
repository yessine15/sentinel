"""Smoke test: the api package imports and exposes a version.

The point of this test in T0.8 is just to give the CI pytest job something
to run. Real tests come with later tasks.
"""

import sentinel_api


def test_version_is_string():
    assert isinstance(sentinel_api.__version__, str)


def test_version_starts_with_zero():
    # Anchor at 0.x for the Phase 0 / pre-alpha era.
    assert sentinel_api.__version__.startswith("0.")
