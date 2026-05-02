#!/bin/bash
while true; do
  curl -s -X POST -H "Content-Type: application/json" -d '{"command": "mcp_quorus_check_messages", "params": {}}' http://localhost:8080/execute
  sleep 90
done
