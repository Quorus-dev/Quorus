#!/bin/bash
RELAY="http://localhost:8080"
SECRET="dev"
ROOM="murmur-dev"
NAME="Aravs-baby-boy"

# Join the room first
curl -s -X POST "$RELAY/rooms/$ROOM/join" \
    -H "Authorization: Bearer $SECRET" \
    -H "Content-Type: application/json" \
    -d '{"participant": "Aravs-baby-boy"}' > /dev/null

MESSAGES=(
    "active on both rooms. what needs to get done?"
    "CI live on GitHub Actions — lint, tests, Redis sidecar. 738 passing."
    "auto-pull running every 60s. catching every commit from Aarya."
    "what's the next feature to ship before April 20?"
    "pull swarm needs real agent coordination tests. should we write them?"
    "murmur context is the killer feature — zero vector DB, pure signal filtering."
    "checking in. anything blocking?"
)

i=0
while true; do
    MSG="${MESSAGES[$((i % ${#MESSAGES[@]}))]}"
    curl -s -X POST "$RELAY/rooms/$ROOM/messages" \
        -H "Authorization: Bearer $SECRET" \
        -H "Content-Type: application/json" \
        -d "{\"from_name\": \"$NAME\", \"content\": \"$MSG\"}" > /dev/null
    echo "[local heartbeat] sent: $MSG"
    i=$((i+1))
    sleep 120
done
