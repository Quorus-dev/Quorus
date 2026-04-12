#!/bin/bash
# Auto-pull Murmur changes from origin every 60s
cd /Users/aravkekane/Desktop/Murmur
while true; do
    git fetch origin main --quiet 2>/dev/null
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/main)
    if [ "$LOCAL" != "$REMOTE" ]; then
        echo "[autopull] New commits detected, pulling..."
        git pull origin main --rebase --quiet 2>&1
        echo "[autopull] Pull complete: $(git log --oneline -1)"
    fi
    sleep 60
done
