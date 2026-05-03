"""OS-level notification dispatch for Quorus chat events.

Single source of truth for surfacing @-mentions / DMs on the user's
desktop notification center, regardless of which Quorus surface (TUI,
reflexd daemon, watcher) detected the message.

Design tenets:

* **Argv only** — never string-interpolate user content into ``osascript``
  or shell commands. The AppleScript is parameterised; values flow in via
  the ``argv`` array (mirrors :mod:`quorus.integrations.watcher` which has
  the same hardening for the same reason).
* **Sanitise then truncate** — strip control chars, then cap title at 80
  chars and body at 200. Both protect against OS-side rendering bugs and
  cap the blast radius of a hostile sender posting a 10MB string.
* **PII guard** — message *content* is NEVER logged to disk or stderr.
  Only the sender, room, and a counter end up in the per-host state file.
* **Rate limit per (sender, room)** — at most one notification per 5-second
  window. Persisted in ``~/.quorus/notifications-state.json`` (0o600) so
  multiple reflexd instances don't all fire concurrently for the same
  message.
* **Best-effort** — every external call is wrapped; ``notify()`` returns a
  bool but never raises. Callers shouldn't have to gate on platform.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("quorus.notifications")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = Path.home() / ".quorus"
STATE_FILE_NAME = "notifications-state.json"
LOG_FILE_NAME = "notifications.log"

# OS notification renderers truncate hard at varying limits — cap conservatively.
MAX_TITLE_LEN = 80
MAX_BODY_LEN = 200

# Rate-limit window per (sender, room).
RATE_LIMIT_SECONDS = 5.0

# Subprocess hard timeout — we never wait long for a banner.
SUBPROCESS_TIMEOUT_S = 5.0

# Strip every C0 / DEL control char except literal whitespace (tab, newline,
# CR are stripped because OS banners render them as garbage glyphs and
# multi-line bodies break some renderers).
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------


def _sanitize(text: str, *, max_len: int) -> str:
    """Strip control characters and truncate to ``max_len``.

    The truncation step keeps the first ``max_len - 1`` chars and appends
    a single ellipsis so the user can tell the body was cut. We do NOT
    attempt UTF-8 boundary-safe slicing because Python str slicing is
    code-point based; OS notification APIs accept arbitrary code points.
    """
    if not isinstance(text, str):
        text = str(text or "")
    flat = _CTRL_RE.sub(" ", text)
    flat = flat.strip()
    if len(flat) <= max_len:
        return flat
    return flat[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Rate-limit state — JSON file at ~/.quorus/notifications-state.json (0o600)
# ---------------------------------------------------------------------------


def _state_path(state_dir: Path | None = None) -> Path:
    base = state_dir or DEFAULT_STATE_DIR
    return base / STATE_FILE_NAME


def _log_path(state_dir: Path | None = None) -> Path:
    base = state_dir or DEFAULT_STATE_DIR
    return base / LOG_FILE_NAME


def _load_state(path: Path) -> dict[str, Any]:
    """Read the rate-limit state, returning an empty dict on any failure.

    Failure modes (missing file, malformed JSON, permission errors) MUST NOT
    raise — a corrupted state file should reset the rate limiter, not break
    notifications entirely.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    """Persist state atomically with 0o600 perms.

    We write to a tmp file in the same dir, ``os.replace`` it into place,
    and then chmod (a no-op on Windows). Any failure is logged but never
    raised — notifications must not block on disk hiccups.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            # Some filesystems (e.g. Windows on FAT) don't honour chmod.
            pass
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("notifications: state save failed: %s", exc)


def _rate_limit_key(sender: str, room: str) -> str:
    """Stable key for the (sender, room) tuple. ``room`` may be empty for DMs."""
    return f"{sender}\x1f{room}"


def _check_and_update_rate_limit(
    *, sender: str, room: str, now: float, state_dir: Path | None,
) -> bool:
    """Return True iff this notification is allowed (and update state)."""
    path = _state_path(state_dir)
    state = _load_state(path)
    last_seen = state.get("last_seen", {})
    counts = state.get("counts", {})
    key = _rate_limit_key(sender, room)
    last = last_seen.get(key)
    if isinstance(last, (int, float)) and (now - last) < RATE_LIMIT_SECONDS:
        return False
    last_seen[key] = now
    counts[key] = int(counts.get(key, 0)) + 1
    state["last_seen"] = last_seen
    state["counts"] = counts
    _save_state(path, state)
    return True


# ---------------------------------------------------------------------------
# Platform dispatch
# ---------------------------------------------------------------------------

# AppleScript that consumes title/subtitle/body/sound from argv. Keeping the
# script literal-only means user content cannot break out of the string.
# `display notification` arg ordering: BODY with title TITLE subtitle SUBTITLE.
_OSASCRIPT_BODY = (
    'on run argv\n'
    '  set _title to item 1 of argv\n'
    '  set _subtitle to item 2 of argv\n'
    '  set _body to item 3 of argv\n'
    '  set _sound to item 4 of argv\n'
    '  if _sound is "" then\n'
    '    display notification _body with title _title subtitle _subtitle\n'
    '  else\n'
    '    display notification _body with title _title subtitle _subtitle sound name _sound\n'
    '  end if\n'
    'end run'
)


def _macos_argv(
    *, title: str, body: str, sender: str, sound: str | None,
) -> list[str]:
    """Build the EXACT osascript argv. Exposed so tests can pin the shape."""
    return [
        "osascript",
        "-e", _OSASCRIPT_BODY,
        "--",
        title,
        sender,
        body,
        sound or "",
    ]


def _linux_argv(*, title: str, body: str) -> list[str]:
    """Build the notify-send argv. ``--app-name`` and ``--urgency`` pinned."""
    return [
        "notify-send",
        "--app-name=Quorus",
        "--urgency=normal",
        title,
        body,
    ]


def _dispatch_macos(
    *, title: str, body: str, sender: str, sound: bool,
) -> bool:
    sound_name = "Glass" if sound else None
    argv = _macos_argv(title=title, body=body, sender=sender, sound=sound_name)
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=SUBPROCESS_TIMEOUT_S, check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("notifications: osascript failed: %s", exc)
        return False


def _dispatch_linux(*, title: str, body: str) -> bool:
    argv = _linux_argv(title=title, body=body)
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=SUBPROCESS_TIMEOUT_S, check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("notifications: notify-send failed: %s", exc)
        return False


def _dispatch_fallback(
    *, sender: str, room: str, state_dir: Path | None,
) -> bool:
    """Append a content-free debug line to ~/.quorus/notifications.log.

    Logs ONLY sender + room + timestamp. Message content is never written
    to disk by this fallback (PII guard).
    """
    path = _log_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({
                    "ts": time.time(),
                    "sender": sender,
                    "room": room,
                    "platform": sys.platform,
                }) + "\n",
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.debug("notifications: fallback log failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify(
    title: str,
    body: str,
    *,
    sender: str = "",
    room: str = "",
    sound: bool = True,
    focus_action: str | None = None,  # noqa: ARG001 — reserved for future
    state_dir: Path | None = None,
    now: float | None = None,
) -> bool:
    """Surface ``title`` / ``body`` on the host's native notification UI.

    Parameters
    ----------
    title, body
        User-visible strings. Sanitised and truncated; safe to pass any text.
    sender, room
        Used only for rate limiting + debug log. Never rendered into the
        AppleScript or shell command line.
    sound
        macOS only — plays the "Glass" alert sound when True.
    focus_action
        Reserved for a future "click to focus terminal" deep link. Accepted
        but ignored today so callers can pass it without breaking.
    state_dir
        Override for the state-file directory (used by tests).
    now
        Override for the rate-limit clock (used by tests).

    Returns
    -------
    bool
        True iff a banner was successfully dispatched. False on rate-limit,
        unsupported platform, or subprocess failure. Never raises.
    """
    safe_title = _sanitize(title, max_len=MAX_TITLE_LEN)
    safe_body = _sanitize(body, max_len=MAX_BODY_LEN)
    safe_sender = _sanitize(sender, max_len=MAX_TITLE_LEN)
    safe_room = _sanitize(room, max_len=MAX_TITLE_LEN)

    if not safe_title and not safe_body:
        return False

    clock = now if now is not None else time.time()
    if not _check_and_update_rate_limit(
        sender=safe_sender, room=safe_room, now=clock, state_dir=state_dir,
    ):
        # Don't log content; just record that we suppressed.
        logger.debug(
            "notifications: rate-limited sender=%s room=%s",
            safe_sender or "?", safe_room or "?",
        )
        return False

    platform = sys.platform
    if platform == "darwin":
        return _dispatch_macos(
            title=safe_title, body=safe_body, sender=safe_sender, sound=sound,
        )
    if platform.startswith("linux"):
        return _dispatch_linux(title=safe_title, body=safe_body)
    return _dispatch_fallback(
        sender=safe_sender, room=safe_room, state_dir=state_dir,
    )
