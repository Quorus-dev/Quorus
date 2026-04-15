FROM python:3.12-slim AS base

WORKDIR /app

# Install pinned dependencies from lockfile (reproducible builds).
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy source before installing the package — hatchling's build picks up
# every entry in pyproject.toml's [tool.hatch.build.targets.wheel].packages,
# which includes quorus/ plus packages/*/quorus_* for the monorepo split.
COPY pyproject.toml README.md alembic.ini ./
COPY quorus/ quorus/
COPY packages/ packages/

# Install the package (no deps — those came from requirements.lock above)
RUN pip install --no-cache-dir --no-deps .

# Non-root user for security
RUN useradd --create-home --shell /bin/bash quorus \
    && mkdir -p /app/data \
    && chown -R quorus:quorus /app/data

USER quorus

# Persist messages across restarts (fallback when Postgres unavailable)
VOLUME ["/app/data"]
ENV MESSAGES_FILE=/app/data/messages.json

EXPOSE 8080
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD python -m uvicorn quorus.relay:app --host 0.0.0.0 --port ${PORT:-8080}
