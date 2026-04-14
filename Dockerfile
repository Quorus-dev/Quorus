FROM python:3.12-slim AS base

WORKDIR /app

# Install pinned dependencies from lockfile (reproducible builds).
# This layer caches independently of application source.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy source before installing the package — hatchling's build picks up
# every entry in pyproject.toml's [tool.hatch.build.targets.wheel].packages,
# which includes murmur/ plus packages/*/murmur_* for the monorepo split.
# Previously we copied only murmur/__init__.py before install to cache the
# install layer; that silently dropped the new packages/* subpackages and
# broke imports like `from murmur.sdk import Room` at runtime.
COPY pyproject.toml README.md alembic.ini ./
COPY murmur/ murmur/
COPY packages/ packages/

# Install the package (no deps — those came from requirements.lock above)
RUN pip install --no-cache-dir --no-deps .

# Non-root user for security
RUN useradd --create-home --shell /bin/bash murmur \
    && mkdir -p /app/data \
    && chown -R murmur:murmur /app/data

USER murmur

# Persist messages across restarts (fallback when Postgres unavailable)
VOLUME ["/app/data"]
ENV MESSAGES_FILE=/app/data/messages.json

EXPOSE 8080
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD python -m uvicorn murmur.relay:app --host 0.0.0.0 --port ${PORT:-8080}
