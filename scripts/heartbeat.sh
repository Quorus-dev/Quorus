#!/bin/bash
# Send a message to test-room every 120s
RELAY="http://100.127.251.47:9000"
SECRET="medport-secret"
ROOM="test-room"
NAME="Aravs-baby-boy"

MESSAGES=(
    "still here, building. what's the status on your end?"
    "any new commits from Aarya? auto-pull is watching."
    "CI is green — 738 tests passing. what are we shipping next?"
    "thinking about the managed service scope. B2C API keys are the right call."
    "murmur is going to be the backbone of every hackathon we run. this is the infra."
    "checking in. anything blocking you?"
    "the pull swarm model is going to be wild once we have 4+ agents coordinating."
    "summary cascade is live — murmur context gives every agent the full picture instantly."
    "April 20 release is real. what do we need to cut to ship clean?"
    "auto-pulling every 60s. I see every commit as it lands."
)

i=0
while true; do
    MSG="${MESSAGES[$((i % ${#MESSAGES[@]}))]}"
    curl -s -X POST "$RELAY/rooms/$ROOM/messages" \
        -H "Authorization: Bearer $SECRET" \
        -H "Content-Type: application/json" \
        -d "{\"from_name\": \"$NAME\", \"content\": \"$MSG\"}" > /dev/null
    echo "[heartbeat] sent: $MSG"
    i=$((i+1))
    sleep 120
done
