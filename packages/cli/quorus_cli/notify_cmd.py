"""`quorus notify on|off|status`: banners for your @-mentions while the hub is closed.

`on` installs a small launchd job that runs quorus_tui.notify_listener for the
current profile (it starts at login too); `off` removes it. The listener only
reads the relay's stream, so turning it on never changes what anyone sees.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from quorus_cli import agent_cmd

LABEL_PREFIX = "dev.quorus.notify."


def _label(name: str) -> str:
    return LABEL_PREFIX + name


def _env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1"}
    for key in ("QUORUS_CONFIG_DIR", "QUORUS_PROFILE", "HOME"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def cmd_notify(args: Any, console: Any) -> None:
    from quorus.config import load_config

    action = getattr(args, "notify_action", None) or "status"
    name = (load_config().get("instance_name") or "").strip()
    if not name:
        raise SystemExit("notify: no profile yet. Run `quorus init` (or `quorus join`) first.")
    label = _label(name)
    if not agent_cmd._launchd_ok():
        console.print("Background banners need macOS launchd. Elsewhere run "
                      f"`{sys.executable} -m quorus_tui.notify_listener &`.")
        return
    plist = agent_cmd._LA / f"{label}.plist"
    target = f"gui/{agent_cmd._uid()}/{label}"
    if action == "status":
        pid = agent_cmd._launchd_jobs().get(label)
        state = (f"on (pid {pid})" if pid else "on, but not running") if label in \
            agent_cmd._launchd_jobs() else "off"
        console.print(f"Banners for @{name} with the hub closed: {state}")
        return
    subprocess.run(["launchctl", "bootout", target], capture_output=True, check=False,
                   timeout=15)
    if action == "off":
        plist.unlink(missing_ok=True)
        console.print(f"✓ Off: no banners for @{name} while the hub is closed.")
        return
    log = agent_cmd._agents_dir() / "notify.log"
    argv = [sys.executable, "-m", "quorus_tui.notify_listener"]
    plist.parent.mkdir(parents=True, exist_ok=True)
    agent_cmd._write_private(plist, agent_cmd._plist(label, argv, _env(), log))
    subprocess.run(["launchctl", "enable", target], capture_output=True, check=False,
                   timeout=15)
    done = subprocess.run(["launchctl", "bootstrap", f"gui/{agent_cmd._uid()}", str(plist)],
                          capture_output=True, text=True, check=False, timeout=15)
    if done.returncode != 0:
        plist.unlink(missing_ok=True)
        said = (done.stderr or "").strip().splitlines()[:1] or [f"exit {done.returncode}"]
        raise SystemExit(f"notify: couldn't start: launchctl said {said[0]}")
    console.print(f"✓ On: a desktop banner when someone @-mentions @{name} or DMs you, "
                  "even with the hub closed. Turn it off with `quorus notify off`.")
    console.print("  First time on macOS: allow notifications for Script Editor in "
                  "System Settings → Notifications. Check with `quorus notify-test`.")


__all__ = ["cmd_notify", "LABEL_PREFIX"]
