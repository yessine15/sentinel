# =============================================================
# Dockerfile for Sentinel demo API
#
# Multi-stage build: build deps in a heavyweight "builder" stage, copy
# only the installed packages + our code into a slim final image.
#
# Image: ghcr.io/yessine15/sentinel-demo-api:<tag>
# Runs:  uvicorn sentinel_api:app --host 0.0.0.0 --port 8000
# =============================================================

# ---- Stage 1: builder ----------------------------------------
# Use a Python slim image that already has pip + venv tools.
FROM python:3.12-slim AS builder

# Install uv (fast Python package manager) — single static binary.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Build deps into a virtual env in /opt/venv so we can copy it wholesale
# into the final image.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINKER=system \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install the project's base deps first (better layer caching — deps don't
# change often, our code does). We use --no-install-project so uv installs
# the deps WITHOUT copying our code in yet. Base deps include fastapi +
# uvicorn, which is all the demo API needs.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# Now copy the actual app code and install the project itself on top.
COPY api/ ./api/
RUN uv sync --no-dev --no-editable


# ---- Stage 2: runtime ----------------------------------------
# Same Python major.minor, slim base, no build toolchain.
FROM python:3.12-slim AS runtime

# Don't write .pyc files at runtime (saves disk, avoids stale cache).
# PYTHONPATH=/app/api so `import sentinel_api` finds the package uv
# installed there.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/api" \
    PATH="/opt/venv/bin:${PATH}"

# Create non-root user for security. The container will run as this user.
RUN groupadd --system --gid 1001 sentinel \
    && useradd --system --uid 1001 --gid sentinel --create-home --home-dir /home/sentinel sentinel

WORKDIR /app

# Copy the venv (deps + our installed package) from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the app source so /docs (FastAPI's auto-doc UI) can find templates
# and so we can run `uvicorn sentinel_api:app` from /app.
COPY --from=builder /app /app

USER sentinel
EXPOSE 8000

# Health probe hint for orchestrators that read HEALTHCHECK (docker run).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

# Run the FastAPI app with uvicorn, single worker (Phase 0). For higher
# load later we'd use --workers N or gunicorn + uvicorn workers.
CMD ["uvicorn", "sentinel_api:app", "--host", "0.0.0.0", "--port", "8000"]
