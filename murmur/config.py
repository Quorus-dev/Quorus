import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / "mcp-tunnel"
LEGACY_CONFIG_DIR = Path.home() / "claude-tunnel"
CONFIG_FILENAME = "config.json"


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def resolve_config_file() -> Path:
    config_dir_override = os.environ.get("MCP_TUNNEL_CONFIG_DIR")
    if config_dir_override:
        return Path(config_dir_override).expanduser() / CONFIG_FILENAME

    default_file = DEFAULT_CONFIG_DIR / CONFIG_FILENAME
    legacy_file = LEGACY_CONFIG_DIR / CONFIG_FILENAME
    if default_file.exists():
        return default_file
    if legacy_file.exists():
        return legacy_file
    return default_file


def load_config() -> dict[str, Any]:
    """Load config from disk, then apply env-var overrides."""
    file_config: dict[str, Any] = {}
    config_file = resolve_config_file()
    if config_file.exists():
        try:
            file_config = json.loads(config_file.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    def get(env_key: str, file_key: str, default: str) -> str:
        return os.environ.get(env_key) or file_config.get(file_key, default)

    enable_background_polling = os.environ.get("ENABLE_BACKGROUND_POLLING")
    if enable_background_polling is None:
        enable_background_polling = os.environ.get("ENABLE_CHANNELS")
    if enable_background_polling is None:
        enable_background_polling = file_config.get("enable_background_polling")
    if enable_background_polling is None:
        enable_background_polling = file_config.get("enable_channels")

    notification_method = os.environ.get("PUSH_NOTIFICATION_METHOD")
    if notification_method is None:
        notification_method = file_config.get("push_notification_method")

    notification_channel = os.environ.get("PUSH_NOTIFICATION_CHANNEL")
    if notification_channel is None:
        notification_channel = file_config.get("push_notification_channel", "mcp-tunnel")

    if notification_method is None and as_bool(enable_background_polling, default=False):
        notification_method = "notifications/claude/channel"

    # Poll mode: "sse" (realtime push, default) or "lazy" (manual only).
    # "poll" is removed — SSE is the only supported background delivery mode.
    poll_mode = os.environ.get("POLL_MODE") or file_config.get("poll_mode")
    if poll_mode is None:
        # Backward compat: enable_background_polling=true → sse (still supported)
        # New default: sse, always. Polling is gone.
        poll_mode = "sse"
    poll_mode = poll_mode.strip().lower()
    if poll_mode not in {"lazy", "sse"}:
        poll_mode = "sse"

    # When SSE is active and no push method configured, auto-wire Claude channel.
    if poll_mode == "sse" and notification_method is None:
        notification_method = "notifications/claude/channel"

    # API key for production auth (exchanges for JWT)
    api_key = os.environ.get("API_KEY") or file_config.get("api_key", "")

    return {
        "config_file": str(config_file),
        "relay_url": get("RELAY_URL", "relay_url", "http://localhost:8080"),
        "relay_secret": get("RELAY_SECRET", "relay_secret", ""),
        "api_key": api_key,
        "instance_name": get("INSTANCE_NAME", "instance_name", "default"),
        "enable_background_polling": as_bool(enable_background_polling, default=True),
        "poll_mode": poll_mode,
        "push_notification_method": notification_method,
        "push_notification_channel": notification_channel,
    }
