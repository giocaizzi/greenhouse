# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.13

# ── Builder ──────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Resolve dependencies first (no source) for cache efficiency.
COPY pyproject.toml uv.lock ./
COPY libs/greenhouse-core/pyproject.toml ./libs/greenhouse-core/
COPY libs/greenhouse-server/pyproject.toml ./libs/greenhouse-server/
COPY libs/greenhouse-cli/pyproject.toml ./libs/greenhouse-cli/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Now copy the workspace source and install editable workspace packages.
COPY libs/ ./libs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    IRRIGATION_DB_URL="sqlite:////app/data/irrigation.db" \
    IRRIGATION_HOST=0.0.0.0 \
    IRRIGATION_PORT=8000

# Non-root user
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the venv and the workspace sources (editable installs reference them).
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/libs /app/libs

# Volume for the SQLite archive — the only writable location at runtime.
RUN mkdir -p /app/data && chown -R app:app /app/data
VOLUME ["/app/data"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/clusters', timeout=3).status < 500 else 1)"

CMD ["greenhouse-server"]
