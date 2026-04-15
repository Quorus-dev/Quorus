import json
import os
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.json"

# These module-level paths are kept for backward compatibility with tests that
# `monkeypatch.setattr("quorus.config.DEFAULT_CONFIG_DIR", ...)`. The active
# resolution happens in `resolve_config_file()` below, which re-evaluates
# `Path.home()` at call time so it follows any runtime HOME changes.
DEFAULT_CONFIG_DIR = Path.home() / ".quorus"
LEGACY_CONFIG_DIR = Path.home() / "mcp-tunnel"  # Previous default


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
    config_dir_override = os.environ.get("QUORUS_CONFIG_DIR") or os.environ.get("MCP_TUNNEL_CONFIG_DIR")
    if config_dir_override:
        return Path(config_dir_override).expanduser() / CONFIG_FILENAME

    # Prefer module-level constants when they've been monkeypatched in tests;
    # fall back to `Path.home()` at call time otherwise so this works under
    # runtime HOME changes (e.g., `sudo -E`).
    default_dir = DEFAULT_CONFIG_DIR if "DEFAULT_CONFIG_DIR" in globals() else (Path.home() / ".quorus")
    legacy_dir = LEGACY_CONFIG_DIR if "LEGACY_CONFIG_DIR" in globals() else (Path.home() / "mcp-tunnel")
    default_file = default_dir / CONFIG_FILENAME
    legacy_file = legacy_dir / CONFIG_FILENAME
    legacy_murmur_file = Path.home() / ".murmur" / CONFIG_FILENAME
    if default_file.exists():
        return default_file
    if legacy_murmur_file.exists():
        return legacy_murmur_file
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
        notification_channel = file_config.get("push_notification_channel", "quorus")

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
