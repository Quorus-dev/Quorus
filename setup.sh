#!/usr/bin/env bash
# Quorus Quick Setup
# Usage: ./setup.sh <your-name> [relay-url] [secret]
#
# Examples:
#   ./setup.sh arav                          # Local relay
#   ./setup.sh arav https://xxx.ngrok.io     # Remote relay
#   ./setup.sh arav https://xxx.ngrok.io my-secret

set -e

NAME="${1:?Usage: ./setup.sh <your-name> [relay-url] [secret]}"
RELAY_URL="${2:-http://localhost:8080}"
SECRET="${3:-quorus-dev}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Setting up Quorus for: $NAME"
echo "  Relay: $RELAY_URL"
echo ""

# Write config with restrictive perms
mkdir -p ~/.quorus
umask 077
cat > ~/.quorus/config.json <<EOF
{
  "relay_url": "$RELAY_URL",
  "relay_secret": "$SECRET",
  "instance_name": "$NAME",
  "enable_background_polling": true,
  "push_notification_method": "notifications/claude/channel",
  "push_notification_channel": "quorus"
}
EOF
chmod 0600 ~/.quorus/config.json
echo "Config written to ~/.quorus/config.json"

echo ""
echo "Add this to your Claude Code MCP settings:"
echo ""
echo "  Name: quorus"
echo "  Command: uv run --directory $SCRIPT_DIR python -m quorus.mcp_server"
echo ""
echo "Or add to ~/.claude.json:"
cat <<EOF
{
  "mcpServers": {
    "quorus": {
      "command": "uv",
      "args": ["run", "--directory", "$SCRIPT_DIR", "python", "-m", "quorus.mcp_server"]
    }
  }
}
EOF

echo ""
echo "Start the relay:"
echo "  cd $SCRIPT_DIR && RELAY_SECRET=$SECRET uv run quorus-relay"
echo ""
echo "Or just open the hub:"
echo "  quorus"
