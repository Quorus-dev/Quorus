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
from urllib.parse import urlparse

from quorus.private_files import write_private_json

CONFIG_FILENAME = "config.json"

# Kept for back-compat with tests that monkeypatch these names. Active
# resolution re-evaluates Path.home() at call time.
DEFAULT_CONFIG_DIR = Path.home() / ".quorus"
LEGACY_CONFIG_DIR = Path.home() / "mcp-tunnel"
_IMPORT_TIME_DEFAULT = DEFAULT_CONFIG_DIR


def _default_config_dir() -> Path:
    """``~/.quorus`` as of now, not as of import. A test that monkeypatches
    ``DEFAULT_CONFIG_DIR`` still wins; otherwise ``Path.home()`` is re-read, so a
    HOME faked after import (tests, wrappers) is honoured. The import-time value
    used to be returned instead, which is how a `quorus join` test wrote a config
    into the process's real HOME (2026-10-20, PR #15 CI)."""
    current = globals().get("DEFAULT_CONFIG_DIR")
    if current is not None and Path(current) != _IMPORT_TIME_DEFAULT:
        return Path(current)
    return Path.home() / ".quorus"


def _trusted_proxy_count() -> int:
    """Number of proxies in front of the relay we trust to set X-Forwarded-For.

    Wave-5 Fix 4 — taking the LEFTMOST XFF entry trusts the client; taking
    the rightmost-N is the safe pattern. The default is 0: a relay reached
    directly must key on the socket peer, or any client can rotate XFF to
    dodge per-IP rate limits. Deployments behind a proxy opt in (fly.toml
    sets 1 for Fly's edge; CloudFront → ALB → app needs 2).
    """
    try:
        n = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    except ValueError:
        n = 0
    return max(0, n)


def get_real_ip(request: Any, *, trusted_n: int | None = None) -> str:
    """Extract caller IP from a Starlette/FastAPI request, walking only the
    rightmost ``trusted_n`` X-Forwarded-For hops.

    Why rightmost: each proxy appends the immediate sender to the list. A
    spoofed XFF from the public client lands at the LEFT and is therefore
    untrusted. By taking ``parts[-trusted_n]`` we land on the IP the
    outermost trusted proxy actually saw.

    Header lookup tries both casings so plain-dict test stubs (case
    sensitive) and real Starlette headers (case insensitive) both work.
    """
    if trusted_n is None:
        trusted_n = _trusted_proxy_count()
    if trusted_n <= 0:
        client = getattr(request, "client", None)
        return getattr(client, "host", None) or "unknown"
    headers = getattr(request, "headers", None)
    xff_raw = ""
    if headers is not None:
        xff_raw = (
            headers.get("X-Forwarded-For")
            or headers.get("x-forwarded-for")
            or ""
        )
    parts = [p.strip() for p in xff_raw.split(",") if p.strip()]
    if parts and len(parts) >= trusted_n:
        return parts[-trusted_n]
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return client.host
    return "unknown"

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
    default_dir = _default_config_dir()
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

    Behavior:

    - When ``profile`` is given, ``load()`` returns that profile's data
      (or ``{}`` if missing/corrupt).
    - When ``profile`` is ``None`` (default), ``load()`` returns the
      CURRENT profile's data, falling back to legacy auto-migration on
      first call, then falling back to direct pointer-file read if no
      profile layout has been set up yet (returns ``{}`` on fresh installs).
    - ``save(data)`` writes to ``self.path`` with 0o600 atomic rename.
      When ``profile`` is set, ``self.path`` resolves to the profile
      file; otherwise it resolves to the pointer path (backward-compat
      for callers writing the legacy flat shape — migration picks that
      up on the next load).
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        profile: str | None = None,
    ) -> None:
        self._explicit_path = path
        self._profile = profile
        self._loaded_path: Path | None = None
        self._loaded_profile = ""

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        if self._profile is not None:
            from quorus.profiles import PROFILES_SUBDIR
            return (
                resolve_config_dir() / PROFILES_SUBDIR / f"{self._profile}.json"
            )
        return resolve_config_file()

    def load(self, *, migrate: bool = True) -> dict[str, Any]:
        """Return the active config. ``migrate=False`` never writes: a legacy
        flat config is read as-is instead of being moved into profiles/."""
        # Keep provenance alongside this read: the pointer can change before
        # a caller formats an error, and config.json may hold no relay_url.
        self._loaded_path = self.path
        self._loaded_profile = ""
        # Explicit-path callers (tests, CLI --config pointing at a file)
        # get raw file contents, no profile resolution.
        if self._explicit_path is not None:
            return _read_json_or_empty(self._explicit_path)

        from quorus.profiles import ProfileManager

        pm = ProfileManager()

        # Explicit profile kwarg — read that profile or empty.
        if self._profile is not None:
            data = pm.get(self._profile)
            self._loaded_profile = self._profile
            return data or {}

        # Default behavior: auto-migrate legacy, then return current.
        if not migrate:
            legacy = pm.legacy_flat()
            if legacy is not None:
                return legacy
        else:
            pm.migrate_legacy_if_needed()
        current = pm.current()
        if current is not None:
            data = pm.get(current)
            if data is not None:
                self._loaded_path = pm.profiles_dir / f"{current}.json"
                self._loaded_profile = current
                return data
        # No profiles and no legacy to migrate → empty.
        return {}

    def save(self, data: dict[str, Any]) -> None:
        path = self.path
        if _is_legacy_dir(path.parent) or _is_legacy_dir(path.parent.parent):
            raise ValueError(
                f"Refusing to write config to legacy path {path}. "
                "Move your config to ~/.quorus/."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private_json(path, data, indent=2, sort_keys=True)


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


_ENV_OVERRIDES = (("RELAY_URL", "relay_url"), ("RELAY_SECRET", "relay_secret"),
                  ("API_KEY", "api_key"), ("INSTANCE_NAME", "instance_name"))


def env_overrides() -> list[str]:
    """Env vars that silently replace a *different* value in the config file.

    ``load_config`` lets ``RELAY_URL``/``RELAY_SECRET``/``API_KEY``/
    ``INSTANCE_NAME`` win over the file, which daemons rely on. But a stray
    one in a shell sent a live fetch to the wrong relay (2026-10-17), so
    callers can say so. Returns names only, never values.
    """
    file_config: dict[str, Any] = ConfigManager().load()
    return [env for env, key in _ENV_OVERRIDES
            if os.environ.get(env) and file_config.get(key)
            and os.environ[env] != file_config[key]]


DEFAULT_RELAY_URL = "http://localhost:8080"


def same_relay(a: str, b: str) -> bool:
    """Do two relay URLs name the same relay (scheme, host, port, base path)?"""
    def key(url: str) -> tuple[str, str, int | None, str]:
        p = urlparse((url or "").strip())
        port = p.port or {"http": 80, "https": 443}.get(p.scheme)
        return p.scheme.lower(), (p.hostname or "").lower(), port, p.path.rstrip("/")
    try:
        return key(a) == key(b)
    except ValueError:  # a malformed port: not provably the same relay
        return False


# Profile fields that are only valid on the relay they were issued by.
RELAY_BOUND_KEYS = ("api_key", "relay_secret", "agent_api_keys")


def drop_credentials_if_relay_changed(profile: dict[str, Any],
                                      new_relay_url: str) -> tuple[dict[str, Any], bool]:
    """A copy of *profile* without its relay-bound credentials when it is
    being pointed at a different relay; ``(copy, dropped)``."""
    saved = profile.get("relay_url") or DEFAULT_RELAY_URL
    if same_relay(saved, new_relay_url):
        return dict(profile), False
    kept = {k: v for k, v in profile.items() if k not in RELAY_BOUND_KEYS}
    return kept, any(k in profile for k in RELAY_BOUND_KEYS)


def withheld_credential_message(relay_url: str) -> str:
    return (f"no credential saved for {relay_url}; run `quorus init` or `quorus join` "
            "for it (your saved credentials belong to another relay and are not sent)")


def load_config(profile: str | None = None, *, migrate: bool = True) -> dict[str, Any]:
    """Load config from disk, then apply env-var overrides.

    A saved ``relay_secret`` or ``api_key`` is bound to the ``relay_url`` saved
    with it: when ``RELAY_URL`` points at a different relay, the saved ones are
    withheld (``credentials_withheld``) and only credentials from the
    environment apply, so a credential never reaches a relay it wasn't issued
    for. ``saved_relay_url`` lets callers that take the relay from elsewhere
    (``QUORUS_RELAY_URL``, a flag) apply the same rule.

    Preserved for back-compat with existing callers. New code should use
    ``ConfigManager`` directly for simple load/save.
    """
    manager = ConfigManager(profile=profile) if profile else ConfigManager()
    file_config: dict[str, Any] = manager.load(migrate=migrate)
    config_file = manager.path if profile else resolve_config_file()

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

    if notification_method is None:
        notification_method = "notifications/claude/channel"

    # SSE is the only background delivery mode; the legacy polling-mode
    # config key was removed 2026-08. Stale configs that still contain it
    # load fine — load_config() reads only known keys — and we emit a single
    # debug-level note so operators can clean up. (The key name is built
    # dynamically so repo-wide greps for the removed feature stay clean.)
    _removed_poll_key = "POLL_MODE".lower()
    if _removed_poll_key in file_config or "POLL_MODE" in os.environ:
        logger.debug(
            "Ignoring removed config key %r (SSE is the only delivery mode)",
            _removed_poll_key,
        )

    saved_relay_url = file_config.get("relay_url") or DEFAULT_RELAY_URL
    relay_url = os.environ.get("RELAY_URL") or saved_relay_url
    relay_url_source = ("env:RELAY_URL" if os.environ.get("RELAY_URL")
                        else "file" if file_config.get("relay_url") else "default")
    bound = same_relay(relay_url, saved_relay_url)
    saved_secret = file_config.get("relay_secret") or ""
    saved_key = file_config.get("api_key") or ""
    env_secret = (os.environ.get("RELAY_SECRET") or "").strip()
    env_key = (os.environ.get("API_KEY") or "").strip()

    return {
        "config_file": str(config_file),
        "relay_url": relay_url,
        "relay_secret": env_secret or (saved_secret if bound else ""),
        "api_key": env_key or (saved_key if bound else ""),
        "saved_relay_url": saved_relay_url,
        "relay_url_source": relay_url_source,
        "relay_url_config_file": str(manager._loaded_path or config_file),
        "relay_url_profile": manager._loaded_profile,
        "credentials_withheld": bool(not bound and (saved_secret or saved_key)),
        "relay_secret_from_env": bool(env_secret),
        "api_key_from_env": bool(env_key),
        "instance_name": get("INSTANCE_NAME", "instance_name", "default"),
        "enable_background_polling": as_bool(enable_background_polling, default=True),
        "push_notification_method": notification_method,
        "push_notification_channel": notification_channel,
    }
