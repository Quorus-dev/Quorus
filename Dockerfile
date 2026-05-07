# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1 — builder: install pinned dependencies + build the wheel.
# Ships pip + build deps + cached wheels.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps + uv (10x faster pip resolver, deterministic locks).
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install into an isolated prefix so we can copy the resolved tree forward
# into the runtime stage without dragging pip / wheels along with it.
ENV PREFIX=/install
RUN mkdir -p "$PREFIX"

# Pinned deps from the lockfile (single source of truth — see pyproject.toml
# header). The lockfile must be regenerated via ``uv pip compile`` whenever
# pyproject.toml changes. Both files commit in the same PR.
COPY requirements.lock ./
RUN pip install --no-cache-dir --prefix="$PREFIX" -r requirements.lock

# Source layout: hatchling's wheel pulls every package listed in
# [tool.hatch.build.targets.wheel].packages — quorus/ + packages/* monorepo.
COPY pyproject.toml README.md alembic.ini ./
COPY quorus/ quorus/
COPY packages/ packages/

# Install Quorus itself (no deps — those came from the lockfile above).
RUN pip install --no-cache-dir --prefix="$PREFIX" --no-deps .


# ---------------------------------------------------------------------------
# Stage 2 — runtime: lean image, no pip / build deps / wheel cache.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

# Carry the resolved Python install forward; pip itself is left behind.
COPY --from=builder /install /usr/local
# Fly release_command runs from /app, so Alembic's config and migration
# scripts must exist outside the installed wheel.
COPY --from=builder /build/alembic.ini /app/alembic.ini
COPY --from=builder /build/quorus/migrations /app/quorus/migrations

# Non-root user for security. Match the data dir's UID so volumes mounted
# with the default UID:GID (1000:1000) work without a bind-mount chown.
RUN useradd --create-home --shell /bin/bash --uid 1000 quorus \
    && mkdir -p /app/data \
    && chown -R quorus:quorus /app/data

USER quorus

# Persist messages across restarts (fallback when Postgres unavailable).
VOLUME ["/app/data"]
ENV MESSAGES_FILE=/app/data/messages.json

EXPOSE 8080
ENV PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD python -m uvicorn quorus.relay:app --host 0.0.0.0 --port ${PORT:-8080}
