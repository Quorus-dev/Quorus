FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies only (cached layer)
COPY pyproject.toml README.md ./
COPY murmur/__init__.py murmur/__init__.py
RUN pip install --no-cache-dir hatchling && pip install --no-cache-dir .

# Copy application code
COPY murmur/ murmur/

# Non-root user for security
RUN useradd --create-home --shell /bin/bash murmur \
    && mkdir -p /app/data \
    && chown -R murmur:murmur /app/data

USER murmur

# Persist messages across restarts
VOLUME ["/app/data"]
ENV MESSAGES_FILE=/app/data/messages.json

EXPOSE 8080
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD python -m uvicorn murmur.relay:app --host 0.0.0.0 --port ${PORT:-8080}
