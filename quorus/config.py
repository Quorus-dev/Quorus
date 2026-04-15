"""Config loading for Quorus.

Single source of truth for where config lives on disk. Precedence (top wins):

1. ``QUORUS_CONFIG_DIR`` env var
2. ``MCP_TUNNEL_CONFIG_DIR`` env var (legacy, still honoured)
3. ``~/.quorus/``
4. ``~/.murmur/`` (legacy, read-only, warns on use)
5. ``~/mcp-tunnel/`` (legacy, read-only, warns on use)

Writes always land on priority 1, 2, or 3. ``ConfigManager.save()`` raises
``ValueError`` if asked to write to a legacy path.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.json"

# Kept for back-compat with tests that monkeypatch these names. Active
# resolution re-evaluates Path.home() at call time.
DEFAULT_CONFIG_DIR = Path.home() / ".quorus"
LEGACY_CONFIG_DIR = Path.home() / "mcp-tunnel"

_LEGACY_MURMUR_DIRNAME = ".murmur"
_LEGACY_MCP_TUNNEL_DIRNAME = "mcp-tunnel"

logger = logging.getLogger(__name__)

# One warning per legacy path per process. Module-level so it survives across
# ConfigManager instances.
_warned_paths: set[Path] = set()


def _warn_legacy(path: Path) -> None:
    if path in _warned_paths:
        return
    _warned_paths.add(path)
    logger.warning(
        "Reading config from deprecated location %s. Move it to ~/.quorus/.",
        path,
    )


def _is_legacy_dir(path: Path) -> bool:
    name = path.name
    return name in {_LEGACY_MURMUR_DIRNAME, _LEGACY_MCP_TUNNEL_DIRNAME}


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


def resolve_config_dir() -> Path:
    """Return the active config directory following the documented precedence.

    This picks the first existing candidate from priorities 1–5. When nothing
    exists yet, it returns priority 3 (``~/.quorus/``) so callers can create
    and write there. Legacy candidates emit a deprecation warning.
    """
    override = (
        os.environ.get("QUORUS_CONFIG_DIR")
        or os.environ.get("MCP_TUNNEL_CONFIG_DIR")
    )
    if override:
        return Path(override).expanduser()

    # Re-evaluate at call time so HOME changes (sudo -E, test monkeypatch of
    # $HOME) are honoured — but respect test monkeypatch of the module-level
    # constants, too.
    default_dir = (
        DEFAULT_CONFIG_DIR
        if "DEFAULT_CONFIG_DIR" in globals()
        else Path.home() / ".quorus"
    )
    legacy_murmur = Path.home() / _LEGACY_MURMUR_DIRNAME
    legacy_mcp = (
        LEGACY_CONFIG_DIR
        if "LEGACY_CONFIG_DIR" in globals()
        else Path.home() / _LEGACY_MCP_TUNNEL_DIRNAME
    )

    if default_dir.exists():
        return default_dir
    if (legacy_murmur / CONFIG_FILENAME).exists():
        _warn_legacy(legacy_murmur)
        return legacy_murmur
    if (legacy_mcp / CONFIG_FILENAME).exists():
        _warn_legacy(legacy_mcp)
        return legacy_mcp
    return default_dir


def resolve_config_file() -> Path:
    return resolve_config_dir() / CONFIG_FILENAME


class ConfigManager:
    """Load/save config with a single source of truth for the path.

    Call sites should prefer ``ConfigManager`` over reimplementing
    load/save in each module.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path

    @property
    def path(self) -> Path:
        return self._explicit_path or resolve_config_file()

    def load(self) -> dict[str, Any]:
        path = self.path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}

    def save(self, data: dict[str, Any]) -> None:
        path = self.path
        if _is_legacy_dir(path.parent):
            raise ValueError(
                f"Refusing to write config to legacy path {path}. "
                "Move your config to ~/.quorus/."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via O_CREAT | 0o600 so the file is never readable by others on
        # shared hosts, then atomically rename into place.
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp, path)


def load_config() -> dict[str, Any]:
    """Load config from disk, then apply env-var overrides.

    Preserved for back-compat with existing callers. New code should use
    ``ConfigManager`` directly for simple load/save.
    """
    config_file = resolve_config_file()
    file_config: dict[str, Any] = ConfigManager(config_file).load()

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
        poll_mode = "sse"
    poll_mode = poll_mode.strip().lower()
    if poll_mode not in {"lazy", "sse"}:
        poll_mode = "sse"

    if poll_mode == "sse" and notification_method is None:
        notification_method = "notifications/claude/channel"

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
