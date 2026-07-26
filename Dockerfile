# =============================================================
# Dockerfile for Sentinel demo API (Phase 2: with agents)
#
# Multi-stage build: build deps in a "builder" stage, copy only the
# installed packages + our code into a slim final image.
#
# Image: ghcr.io/yessine15/sentinel-demo-api:<tag>
# Runs:  uvicorn sentinel_api:app --host 0.0.0.0 --port 8000
# =============================================================

# ---- Stage 1: builder ----------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINKER=system \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install the project's base + agents deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project --extra agents

# Copy all three Python packages and install them.
COPY api/ ./api/
COPY agents/ ./agents/
COPY rag/ ./rag/
RUN uv sync --no-dev --no-editable --extra agents --extra rag


# ---- Stage 2: runtime ----------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/api:/app/rag:/app/agents" \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system --gid 1001 sentinel \
    && useradd --system --uid 1001 --gid sentinel --create-home --home-dir /home/sentinel sentinel

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

USER sentinel
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["uvicorn", "sentinel_api:app", "--host", "0.0.0.0", "--port", "8000"]
