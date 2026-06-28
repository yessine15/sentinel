"""Sentinel API package — FastAPI backend.

The `app` FastAPI instance lives in `sentinel_api.main`. Importing it via
the package (`from sentinel_api import app`) re-exports it for convenience
so uvicorn can be pointed at `sentinel_api:app` directly.
"""

__version__ = "0.1.0"

# Re-export the FastAPI app for `uvicorn sentinel_api:app`.
# Imported lazily to avoid a hard dependency on fastapi when someone just
# wants `__version__`.
try:
    from sentinel_api.main import app as app  # noqa: F401
except ImportError:  # pragma: no cover - happens if fastapi isn't installed
    pass
