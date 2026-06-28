"""Smoke test: the rag package imports and exposes a version."""

import sentinel_rag


def test_version_is_string():
    assert isinstance(sentinel_rag.__version__, str)


def test_version_starts_with_zero():
    assert sentinel_rag.__version__.startswith("0.")
