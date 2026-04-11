FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies only (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application code
COPY relay_server.py tunnel_config.py analytics.py ./

# Non-root user for security
RUN useradd --create-home --shell /bin/bash murmur
USER murmur

# Persist messages across restarts
VOLUME ["/app/data"]
ENV MESSAGES_FILE=/app/data/messages.json

EXPOSE 8080
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python", "-m", "uvicorn", "relay_server:app", "--host", "0.0.0.0", "--port", "8080"]
