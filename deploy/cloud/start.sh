#!/bin/sh
# Start Quorus Cloud: Redis on 127.0.0.1 beside the relay, both as user quorus.
# In account mode rooms and members live in Redis, so it must keep its data on
# the volume: with an in-memory Redis a restart deleted every room (live test,
# 2026-11-09). Runs as root only to hand the freshly mounted volume to quorus.
set -eu
DATA=/app/data/redis
mkdir -p "$DATA"
chown -R quorus:quorus /app/data
as_quorus() { setpriv --reuid=1000 --regid=1000 --init-groups "$@"; }
as_quorus redis-server --bind 127.0.0.1 --port 6379 --dir "$DATA" \
  --appendonly yes --appendfsync everysec --daemonize yes
until redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; do sleep 0.2; done
exec setpriv --reuid=1000 --regid=1000 --init-groups \
  env HOME=/home/quorus python -m uvicorn quorus.relay:app --host 0.0.0.0 --port "${PORT:-8080}"
