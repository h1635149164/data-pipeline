# syntax=docker/dockerfile:1

# ── Stage 1: dependency resolver ─────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

# Copy only the dependency manifests first for layer caching.
COPY pyproject.toml uv.lock ./

# Install production dependencies into a local virtual environment.
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS runtime

WORKDIR /app

# Bring the pre-built venv from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Copy application source.
COPY src/ ./src/
COPY pyproject.toml ./

# Ensure the venv is on PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Non-root user for container security.
RUN adduser --disabled-password --gecos "" pipeline
USER pipeline

ENTRYPOINT ["python", "-m", "src.main"]
