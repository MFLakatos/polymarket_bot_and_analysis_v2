FROM python:3.11-slim AS base

# ── Environment ───────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

# ── Poetry ────────────────────────────────────────────────────────────────────
RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# ── Python deps ───────────────────────────────────────────────────────────────
COPY pyproject.toml README.md ./
# Copy source early so poetry can resolve packages
COPY src ./src

# Install without dev deps; include copy-trading extra by default
RUN poetry install --only main --extras copy-trading --no-interaction --no-ansi

# ── App files ─────────────────────────────────────────────────────────────────
COPY config ./config
COPY cli.py ./cli.py

# Ensure src on PYTHONPATH
ENV PYTHONPATH=/app/src

# ── Default: polymarket-graph CLI ─────────────────────────────────────────────
ENTRYPOINT ["poetry", "run", "polymarket-graph"]
CMD ["--help"]
