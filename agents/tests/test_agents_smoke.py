"""Smoke test: the agents package imports and exposes a version."""

import sentinel_agents


def test_version_is_string():
    assert isinstance(sentinel_agents.__version__, str)


def test_version_starts_with_zero():
    assert sentinel_agents.__version__.startswith("0.")
