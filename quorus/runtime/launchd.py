"""Launchd plist generator — auto-start reflexd-manager on macOS login.

We DO NOT auto-install. ``install_launchd`` writes a plist to a path the
caller picked, and the CLI wrapper (``quorus reflexd-manager install-launchd``)
prompts for confirmation before placing it under ``~/Library/LaunchAgents/``.

The plist runs ``quorus reflexd-manager start`` on user login and asks
launchd to re-spawn the supervisor on crash via ``KeepAlive``. The supervisor
itself is in charge of the per-participant reflexd children — launchd only
watches the supervisor PID.

Linux equivalent: a systemd ``--user`` service template printable to stdout
via :func:`render_systemd_unit`. We deliberately don't auto-install that one
either; distros differ on where ``~/.config/systemd/user/`` even lives.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

LABEL = "dev.quorus.reflexd"
DEFAULT_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render_plist(
    *,
    quorus_bin: str,
    label: str = LABEL,
    log_dir: Path | None = None,
) -> bytes:
    """Build the launchd plist as XML bytes.

    ``quorus_bin`` should be the absolute path to the ``quorus`` entrypoint.
    Caller is responsible for resolving it (e.g. via ``shutil.which``) so
    the plist is reproducible and not dependent on whatever ``$PATH`` looks
    like at boot.
    """
    log_dir = log_dir or (Path.home() / ".quorus")
    stdout_path = str(log_dir / "reflexd-manager.out.log")
    stderr_path = str(log_dir / "reflexd-manager.err.log")

    payload: dict = {
        "Label": label,
        "ProgramArguments": [str(quorus_bin), "reflexd-manager", "start"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
        # Throttle so a crashing supervisor doesn't fork-bomb launchd.
        "ThrottleInterval": 30,
        "ProcessType": "Background",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_launchd(
    plist_path: Path,
    *,
    quorus_bin: str,
    label: str = LABEL,
) -> Path:
    """Write the plist to ``plist_path``. Caller must confirm beforehand.

    The CLI subcommand prompts the user; this function never asks. Mode 0644
    is what launchd expects — anything stricter and ``launchctl load`` will
    silently refuse to read it on some versions of macOS.
    """
    plist_path = Path(plist_path).expanduser()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    data = render_plist(quorus_bin=quorus_bin, label=label)
    plist_path.write_bytes(data)
    try:
        plist_path.chmod(0o644)
    except OSError:
        pass
    return plist_path


def render_systemd_unit(*, quorus_bin: str) -> str:
    """Return a systemd --user service template (Linux). Print, don't install."""
    return f"""\
# Save as ~/.config/systemd/user/quorus-reflexd-manager.service
# Then: systemctl --user daemon-reload && systemctl --user enable --now quorus-reflexd-manager
[Unit]
Description=Quorus reflexd supervisor (multi-agent notification daemon)
After=network-online.target

[Service]
Type=simple
ExecStart={quorus_bin} reflexd-manager start
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""
