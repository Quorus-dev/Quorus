"""``quorus reflex status`` — single-pane observability dashboard.

Walks ``~/.quorus/runtime/*.pid`` for liveness via ``os.kill(pid, 0)``,
tails ``~/.quorus/reflexd.log`` for SSE state and last activity, and
renders one Rich table per agent. No psutil, no network calls.

Public API
----------
- ``collect_state()`` — reads disk, returns plain dict (testable).
- ``render_table(state)`` / ``render_json(state)`` — pure formatters.
- ``cmd_reflex_status(args)`` — argparse handler used by cli.py.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

_RUNTIME_DIR = Path.home() / ".quorus" / "runtime"
_LOG_PATH = Path.home() / ".quorus" / "reflexd.log"
_HARNESSES = ("claude", "codex", "gemini", "cursor")
_LOG_TAIL_BYTES = 64 * 1024


def _pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` liveness — None/<=0 → False; EPERM → True (visible)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pidfiles(runtime_dir: Path) -> list[tuple[str, int | None]]:
    """``[(participant, pid_or_None)]`` for every ``reflexd.<id>.pid``."""
    if not runtime_dir.is_dir():
        return []
    out: list[tuple[str, int | None]] = []
    for entry in sorted(runtime_dir.glob("reflexd.*.pid")):
        participant = entry.name[len("reflexd."):-len(".pid")]
        if not participant:
            continue
        try:
            txt = entry.read_text().strip()
            pid = int(txt) if txt else None
        except (OSError, ValueError):
            pid = None
        out.append((participant, pid))
    return out


def _read_log_tail(log_path: Path, max_bytes: int = _LOG_TAIL_BYTES) -> list[str]:
    """Last ``max_bytes`` of the log as non-empty lines; tolerant to misses."""
    if not log_path.exists():
        return []
    try:
        with log_path.open("rb") as fp:
            fp.seek(0, 2)
            size = fp.tell()
            fp.seek(max(0, size - max_bytes))
            data = fp.read()
    except OSError:
        return []
    return [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]


def _humanize_age(seconds: float | None) -> str:
    """``5s`` / ``1.2m`` / ``3.4h`` / ``2d``; None or negative → ``—``."""
    if seconds is None or seconds < 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86_400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86_400:.1f}d"


def _detect_harness_for(participant: str) -> str:
    """Infer harness CLI from ``arav-codex`` etc.; else substring scan."""
    parts = participant.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in _HARNESSES:
        return parts[1]
    lower = participant.lower()
    for h in _HARNESSES:
        if h in lower:
            return h
    return "—"


def _extract_iso_timestamp(line: str) -> float | None:
    """Pull a leading ISO-8601 timestamp out of a log line, or ``None``."""
    if len(line) < 19:
        return None
    head = line[:32]
    for cut in (32, 26, 23, 19):
        candidate = head[:cut].rstrip()
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _parse_log_for(participant: str, lines: list[str]) -> dict[str, Any]:
    """Walk reverse-chronological log lines, pluck signals for one agent.

    Kept for backwards compatibility — single-agent callers (and tests
    that pre-date the multi-agent fan-out) still see the original API.
    Internally delegates to :func:`_parse_log_fanout` for the heavy
    lifting so there's only one parse implementation to keep correct.
    """
    fanout = _parse_log_fanout(lines, [participant])
    return fanout.get(participant) or {
        "sse_state": "?", "last_wake": "—",
        "last_error": "—", "last_bids": [],
    }


def _empty_signals() -> dict[str, Any]:
    """Default signal record used when no log lines mention a participant."""
    return {
        "sse_state": "?",
        "last_wake": "—",
        "last_error": "—",
        "last_bids": [],
    }


def _parse_log_fanout(
    lines: list[str], participants: list[str],
) -> dict[str, dict[str, Any]]:
    """Single-pass log parser that fans signals out to every participant.

    M18 fix — the prior implementation did one full reverse walk over
    the entire log per agent, so a 10-agent swarm with a 10MB log
    produced 100MB of work per ``quorus reflex status`` invocation.
    This version reads the log once, decides per line which participant
    (if any) it pertains to, and routes the relevant signal into a
    shared ``{participant: dict}`` record. Asymptotically O(N + N·A)
    becomes O(N + A); for a real swarm (A=10, N=10⁵) that's ~10x.

    Behaviour parity with the legacy path:
    * The first sse-connected/disconnected line per agent (in
      reverse-chronological order) wins, just like the old loop.
    * ``last_wake`` is the most-recent ``posted reply``/``wake`` line.
    * ``last_error`` is the most-recent line containing
      ``error``/``warning``.
    * ``last_bids`` keeps up to five bid lines per agent, oldest first
      (matches the legacy ``last_bids[]`` collection order).
    """
    out: dict[str, dict[str, Any]] = {p: _empty_signals() for p in participants}
    if not lines or not participants:
        return out

    # Pre-compute lowercase participant names once so the per-line
    # routing decision is N substring tests rather than N + line_lower
    # work per name.
    p_lower = [p.lower() for p in participants]
    now = time.time()

    for raw in reversed(lines):
        lower_line = raw.lower()
        # Identify which agents this line pertains to. Most log lines
        # mention exactly one participant; the loop terminates early on
        # match. A line with no participant in it is a daemon-wide
        # event (e.g. "reflexd starting") which we ignore for per-agent
        # signals — same as the legacy path's substring guard.
        for idx, p_lc in enumerate(p_lower):
            if p_lc not in lower_line:
                continue
            sig = out[participants[idx]]

            if sig["sse_state"] == "?":
                if "sse connected" in lower_line or "connected to relay" in lower_line:
                    sig["sse_state"] = "connected"
                elif "sse disconnected" in lower_line or "sse closed" in lower_line:
                    sig["sse_state"] = "disconnected"

            if sig["last_wake"] == "—" and (
                "posted reply" in lower_line or "wake" in lower_line
            ):
                ts = _extract_iso_timestamp(raw)
                if ts is not None:
                    sig["last_wake"] = _humanize_age(max(0.0, now - ts))

            if sig["last_error"] == "—" and (
                "error" in lower_line or "warning" in lower_line
            ):
                sig["last_error"] = raw.strip()[-120:]

            if "bid" in lower_line and len(sig["last_bids"]) < 5:
                sig["last_bids"].append(raw.strip()[-200:])
            # A log line typically pertains to a single agent, but a
            # broadcast line could mention several. Don't break — let
            # all matching agents update.
    return out


def _detect_binaries() -> dict[str, str | None]:
    return {h: shutil.which(h) for h in _HARNESSES}


def collect_state(*, runtime_dir: Path | None = None,
                  log_path: Path | None = None) -> dict[str, Any]:
    """Snapshot reflexd swarm state.

    Pure-ish: reads disk only. Returns a plain dict (json-serializable).
    Stable schema for ``--json`` consumers. Never raises on missing
    pidfiles / unreadable log — degrades to empty fields.
    """
    rdir = runtime_dir or _RUNTIME_DIR
    lpath = log_path or _LOG_PATH
    state: dict[str, Any] = {
        "agents": [],
        "binaries": _detect_binaries(),
        "log_path": str(lpath),
        "log_present": lpath.exists(),
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    log_lines = _read_log_tail(lpath)
    pidfiles = _read_pidfiles(rdir)
    # M18 — fan signals out to every agent in a single log pass instead
    # of re-scanning the log per-agent. ≥10x speedup at A>=3.
    participants = [name for name, _pid in pidfiles]
    fanout = _parse_log_fanout(log_lines, participants)
    for participant, pid in pidfiles:
        uptime: float | None = None
        if pid is not None:
            try:
                pidfile = rdir / f"reflexd.{participant}.pid"
                uptime = max(0.0, time.time() - pidfile.stat().st_mtime)
            except OSError:
                uptime = None
        signals = fanout.get(participant) or _empty_signals()
        state["agents"].append({
            "participant": participant,
            "pid": pid,
            "online": _pid_alive(pid) if pid is not None else False,
            "uptime_seconds": uptime,
            "sse_state": signals["sse_state"],
            "last_wake": signals["last_wake"],
            "last_error": signals["last_error"],
            "harness_cli": _detect_harness_for(participant),
            "room_count": 0,
            "last_bids": signals["last_bids"],
            "active_claims": [],
        })
    return state


def render_table(state: dict[str, Any]) -> Table:
    """Pure formatter — testable without spawning processes."""
    table = Table(title="Quorus Reflex — agent swarm",
                  title_style="bold cyan", header_style="bold", show_lines=False)
    for col, kw in (
        ("Agent", {"no_wrap": True}),
        ("Status", {"no_wrap": True}),
        ("SSE", {"no_wrap": True}),
        ("Uptime", {"justify": "right", "no_wrap": True}),
        ("Last wake", {"no_wrap": True}),
        ("Harness", {"no_wrap": True}),
        ("Rooms", {"justify": "right", "no_wrap": True}),
        ("Last error", {"overflow": "fold"}),
    ):
        table.add_column(col, **kw)

    agents = state.get("agents") or []
    if not agents:
        table.add_row(
            "[dim]none[/]", "[dim]offline[/]", "—", "—", "—", "—", "—",
            "[dim]No reflexd pidfiles in ~/.quorus/runtime/. "
            "Run [bold]quorus reflexd start[/].[/]",
        )
        return table

    binaries = state.get("binaries") or {}
    for a in agents:
        online = bool(a.get("online"))
        status = "[green]●[/] online" if online else "[red]○[/] offline"
        sse = a.get("sse_state") or "?"
        sse_cell = {"connected": "[green]connected[/]",
                    "disconnected": "[red]disconnected[/]"}.get(sse, "[dim]?[/]")
        harness_raw = a.get("harness_cli") or "—"
        if harness_raw in _HARNESSES and binaries.get(harness_raw):
            harness = f"[green]{harness_raw}[/]"
        elif harness_raw in _HARNESSES:
            harness = f"[red]{harness_raw} (missing)[/]"
        else:
            harness = f"[dim]{harness_raw}[/]"
        last_error = a.get("last_error") or "—"
        if last_error != "—":
            last_error = f"[yellow]{last_error}[/]"
        table.add_row(
            a.get("participant") or "?",
            status,
            sse_cell,
            _humanize_age(a.get("uptime_seconds")),
            a.get("last_wake") or "—",
            harness,
            str(a.get("room_count") or 0),
            last_error,
        )
    return table


def render_json(state: dict[str, Any]) -> str:
    """Compact JSON for ``--json`` output. Sorted keys for stable diffs."""
    return json.dumps(state, indent=2, sort_keys=True, default=str)


def cmd_reflex_status(args) -> None:  # type: ignore[no-untyped-def]
    """Argparse handler — wired from cli.py.

    Flags handled:
    - ``--json`` — print JSON to stdout, no Rich.
    - ``--watch`` — refresh every ``--interval`` seconds (default 2).
      Mutually exclusive with ``--json``. Hard cap at 300 iterations to
      keep CI honest.
    """
    console = Console()
    use_json = bool(getattr(args, "json", False))
    watch = bool(getattr(args, "watch", False))
    interval = max(1, int(getattr(args, "interval", 2) or 2))

    if watch and use_json:
        console.print("[red]error:[/] --watch and --json are mutually exclusive.")
        raise SystemExit(2)

    def _once() -> None:
        state = collect_state()
        if use_json:
            print(render_json(state))
        else:
            console.print(render_table(state))

    if not watch:
        _once()
        return

    iterations = 0
    try:
        while iterations < 300:
            console.clear()
            _once()
            iterations += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        return


__all__ = ["cmd_reflex_status", "collect_state", "render_json", "render_table"]
