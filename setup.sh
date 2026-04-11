#!/usr/bin/env bash
# Murmur Quick Setup
# Usage: ./setup.sh <your-name> [relay-url] [secret]
#
# Examples:
#   ./setup.sh arav                          # Local relay
#   ./setup.sh arav https://xxx.ngrok.io     # Remote relay
#   ./setup.sh arav https://xxx.ngrok.io my-secret

set -e

NAME="${1:?Usage: ./setup.sh <your-name> [relay-url] [secret]}"
RELAY_URL="${2:-http://localhost:8080}"
SECRET="${3:-murmur-hack}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Setting up Murmur for: $NAME"
echo "  Relay: $RELAY_URL"
echo ""

# Write config
mkdir -p ~/mcp-tunnel
cat > ~/mcp-tunnel/config.json <<EOF
{
  "relay_url": "$RELAY_URL",
  "relay_secret": "$SECRET",
  "instance_name": "$NAME",
  "enable_background_polling": true,
  "push_notification_method": "notifications/claude/channel",
  "push_notification_channel": "mcp-tunnel"
}
EOF
echo "Config written to ~/mcp-tunnel/config.json"

# Show Claude Code MCP config to paste
echo ""
echo "Add this to your Claude Code MCP settings:"
echo ""
echo "  Name: murmur"
echo "  Command: uv run --directory $SCRIPT_DIR python $SCRIPT_DIR/mcp_server.py"
echo ""
echo "Or add to ~/.claude/claude_desktop_config.json:"
echo ""
cat <<EOF
{
  "mcpServers": {
    "murmur": {
      "command": "uv",
      "args": ["run", "--directory", "$SCRIPT_DIR", "python", "$SCRIPT_DIR/mcp_server.py"]
    }
  }
}
EOF

echo ""
echo "Done! Start the relay with:"
echo "  cd $SCRIPT_DIR && RELAY_SECRET=$SECRET uv run python relay_server.py"
echo ""
echo "Launch Claude Code with instant push (recommended):"
echo "  claude --channels server:murmur"
echo ""
echo "Then in Claude Code, your agent has these tools:"
echo "  send_message, check_messages, list_participants"
echo "  send_room_message, join_room, list_rooms"
echo "  start_auto_poll, stop_auto_poll"
echo ""
echo "CLI commands:"
echo "  cd $SCRIPT_DIR && uv run python cli.py create <room-name>"
echo "  cd $SCRIPT_DIR && uv run python cli.py invite <room> <name1> <name2> ..."
echo "  cd $SCRIPT_DIR && uv run python cli.py watch <room>"
echo "  cd $SCRIPT_DIR && uv run python cli.py say <room> \"message\""
