"""Run Quorus on this computer: a local relay the user never has to babysit.

There is no public relay, so a consumer's first `quorus` must leave them with
a working relay on their own machine. Used by the first-run wizard and by the
hub when the saved local relay isn't running (e.g. after a reboot).
"""

from __future__ import annotations

import os
import secrets as _secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from quorus.config import resolve_config_dir

LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def is_local(relay_url: str) -> bool:
    return (urlparse(relay_url).hostname or "") in LOCAL_HOSTS


def new_secret() -> str:
    return _secrets.token_hex(16)


def _healthy(relay_url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{relay_url.rstrip('/')}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _accepts(relay_url: str, secret: str) -> bool:
    try:
        r = httpx.get(f"{relay_url.rstrip('/')}/rooms", timeout=5.0,
                      headers={"Authorization": f"Bearer {secret}"})
    except httpx.HTTPError:
        return False
    return r.status_code not in (401, 403)


def ensure_local_relay(relay_url: str, secret: str) -> tuple[bool, str]:
    """Make sure a relay that accepts *secret* runs at local *relay_url*.

    Returns ``(ok, message)``. Starts a detached relay (survives this
    terminal; state + log in the config dir) when nothing is listening.
    Never kills or replaces a relay someone else started.
    """
    if not is_local(relay_url):
        return _healthy(relay_url), ""
    if _healthy(relay_url):
        if _accepts(relay_url, secret):
            return True, "local relay is running"
        return False, ("a relay is already running on this computer with a different "
                       "secret — stop it, or run `quorus init <name> --secret <its secret>`")
    parsed = urlparse(relay_url)
    cfg = resolve_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "RELAY_SECRET": secret, "PORT": str(parsed.port or 8080),
           "HOST": "127.0.0.1", "ALLOW_LEGACY_AUTH": "1",
           "MESSAGES_FILE": str(cfg / "relay-state.json"), "LOG_LEVEL": "WARNING"}
    log = (cfg / "relay.log").open("ab")
    try:
        subprocess.Popen([sys.executable, "-m", "quorus.relay_cli"], env=env, stdout=log,
                         stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                         cwd=str(Path.home()))
    except OSError as exc:
        return False, f"couldn't start the local relay ({exc}); run `quorus relay` yourself"
    for _ in range(40):
        if _healthy(relay_url, 0.5):
            return True, f"started your local relay (log: {cfg / 'relay.log'})"
        time.sleep(0.25)
    return False, f"the local relay didn't start — see {cfg / 'relay.log'}"
