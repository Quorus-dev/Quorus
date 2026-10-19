"""Drive plain `quorus` in a pseudo-terminal, the way a new user meets it.

Loop C: every finding from a manual new-user pass becomes a test here. The
session runs the worktree's own code in a child process whose terminal is a
pty, with a tmp HOME and a scrubbed environment:

* no inherited secrets, keys or config dirs, and PATH cut to this Python and
  /usr/bin:/bin, so no vendor CLI (claude, codex, ...) can be found;
* ``RELAY_URL`` on a free 127.0.0.1 port, so the first run starts its own relay
  there and never touches 8080 or the dogfood relay;
* ``QUORUS_NO_LAUNCHD=1``: launchd isn't scoped to HOME, so nothing may register
  with the real login session;
* a dead proxy for http(s), loopback exempt, so any network call fails fast.

``expect`` only reads output produced after a mark (by default, the last
action), so an earlier screen can never satisfy a later check. Teardown kills
the session's own process group, then the relay by the pid file in the tmp
HOME, with local_relay's identity check: never by pattern.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_RUN = "import sys; sys.argv = ['quorus']; from quorus_cli.cli import main; main()"
ENTER = "\r"
ESC = "\x1b"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def plain(text: str) -> str:
    """Screen text without escape sequences or carriage returns."""
    return _ANSI.sub("", text).replace("\r", "")


def child_env(home: Path, relay_url: str, extra_path: Path | None = None) -> dict[str, str]:
    # Absolute: the child runs in the tmp HOME, where a relative entry like "."
    # would load some other copy of quorus than the one under test.
    code = [str(Path(p).resolve()) for p in sys.path if p and Path(p).is_dir()]
    dead = "http://127.0.0.1:9"
    # *extra_path* holds a test's stub agent CLIs (F5): still no real vendor CLI.
    path = f"{Path(sys.executable).parent}:/usr/bin:/bin"
    return {
        "HOME": str(home), "PATH": f"{extra_path}:{path}" if extra_path else path,
        "PYTHONPATH": os.pathsep.join(code), "PYTHONDONTWRITEBYTECODE": "1",
        "TERM": "xterm-256color", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
        "COLUMNS": "120", "LINES": "40", "RELAY_URL": relay_url, "QUORUS_NO_LAUNCHD": "1",
        "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
        "http_proxy": dead, "https_proxy": dead, "all_proxy": dead,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }


class PtySession:
    def __init__(self, home: Path, *, cols: int = 120, rows: int = 40,
                 cwd: Path | None = None, extra_path: Path | None = None) -> None:
        """*cwd*: where the user runs `quorus` (default: HOME); a git repo there is
        what the wizard offers to bind. *extra_path*: a directory of stub agent
        CLIs put first on PATH."""
        home.mkdir(parents=True, exist_ok=True)
        self.home = home.resolve()  # /var is /private/var on macOS: compare resolved
        self.cwd = (cwd or home).resolve()
        self.port = free_port()
        self.relay_url = f"http://127.0.0.1:{self.port}"
        self._raw = ""
        self._mark = 0
        self._lock = threading.Lock()
        pid, fd = pty.fork()
        if pid == 0:  # the child: the user's terminal is this pty
            try:
                os.chdir(self.cwd)
                os.execve(sys.executable, [sys.executable, "-c", _RUN],
                          child_env(self.home, self.relay_url, extra_path))
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        # A real terminal always reads what the program writes. Without a drain
        # the pty's output buffer fills while a test waits on the relay, and the
        # hub blocks in tcsetattr(TCSADRAIN): a "bug" no user could ever hit.
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    # ── reading ─────────────────────────────────────────────────────────

    def _drain(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(self.fd, 65536)
            except (OSError, ValueError):  # the child exited, or close() ran
                return
            if not chunk:
                return
            with self._lock:
                self._raw += chunk.decode("utf-8", errors="replace")

    def text(self) -> str:
        with self._lock:
            return self._raw

    def mark(self) -> int:
        """Only output after this point counts for the next ``expect``."""
        self._mark = len(self.text())
        return self._mark

    def expect(self, pattern: str, *, timeout: float = 10.0, since: int | None = None) -> str:
        start = self._mark if since is None else since
        deadline = time.monotonic() + timeout
        rx = re.compile(pattern)
        while True:
            screen = plain(self.text()[start:])
            if rx.search(screen):
                return screen
            if time.monotonic() > deadline:
                raise AssertionError(f"no {pattern!r} within {timeout}s; screen since the "
                                     f"last action ends with:\n{screen[-1500:]}")
            time.sleep(0.05)

    def settle(self, quiet: float = 0.5, timeout: float = 5.0) -> None:
        """Wait until the screen has been quiet for *quiet* seconds."""
        deadline = time.monotonic() + timeout
        seen, since = len(self.text()), time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.05)
            now = len(self.text())
            if now != seen:
                seen, since = now, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return

    # ── writing ─────────────────────────────────────────────────────────

    def type(self, text: str, *, gap: float = 0.01) -> None:
        """Keystrokes, one at a time, like a person typing."""
        self.mark()
        for ch in text:
            os.write(self.fd, ch.encode())
            time.sleep(gap)

    def paste(self, text: str) -> None:
        """One burst, the way a terminal delivers a paste."""
        self.mark()
        os.write(self.fd, text.encode())

    # ── teardown ────────────────────────────────────────────────────────

    def close(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(50):
            if os.waitpid(self.pid, os.WNOHANG) != (0, 0):
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        self._reader.join(timeout=2)
        os.close(self.fd)
        stop_home_agents(self.home)
        self._stop_relay()

    def _stop_relay(self) -> None:
        stop_home_relays(self.home, self.port)


def stop_home_agents(home: Path) -> None:
    """Every wake daemon the session's `quorus agent add` started (F5): they run
    detached, so the session's process group doesn't cover them. Each is found
    by its pid file in the tmp HOME and stopped through agent_proc's identity
    check, with its process group (the stub CLI it was running), never by
    pattern. A daemon that won't stop fails the test."""
    from quorus_cli import agent_proc

    agents_dir = home / ".quorus" / "agents"
    survivors = []
    for pidfile in sorted(agents_dir.glob("*.pid")) if agents_dir.is_dir() else []:
        name = pidfile.stem
        if agent_proc.live_pid(agents_dir, name) is None:
            continue
        if not agent_proc.stop(agents_dir, name, timeout=10):
            survivors.append(name)
    if survivors:
        raise AssertionError(f"wake daemon(s) {survivors} from {home} didn't stop")


def spawned_relays(home: Path) -> list[int]:
    """Pids of every relay process *home*'s relay log says it started."""
    try:
        log = (home / ".quorus" / "relay.log").read_text(errors="replace")
    except OSError:
        return []
    return [int(n) for n in re.findall(r"Started server process \[(\d+)\]", log)]


def stop_home_relays(home: Path, port: int) -> None:
    """Every relay *home* started, not just the one its pid file names: a relay
    the pid file missed (a startup timeout, a second start) must not outlive the
    test (codex, review of e409ee1a). The pid-file relay goes through
    local_relay's identity check; any other is signalled only while it is still a
    `-m quorus.relay_cli` process whose working directory is *home*. A relay that
    can't be stopped fails the test."""
    from quorus.runtime.local_relay import RELAY_ARGV_TAIL, _ps, stop_started_relay

    home = home.resolve()
    saved = os.environ.get("QUORUS_CONFIG_DIR")
    os.environ["QUORUS_CONFIG_DIR"] = str(home / ".quorus")
    try:
        stop_started_relay(port)
    finally:
        if saved is None:
            os.environ.pop("QUORUS_CONFIG_DIR", None)
        else:
            os.environ["QUORUS_CONFIG_DIR"] = saved
    survivors = []
    for pid in spawned_relays(home):
        if not _ps(pid, "command").endswith(RELAY_ARGV_TAIL) or _cwd(pid) != home:
            continue  # already gone, or a reused pid that isn't ours
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not _ps(pid, "command").endswith(RELAY_ARGV_TAIL):  # gone (or a zombie)
                break
            time.sleep(0.1)
        else:
            survivors.append(pid)
    if survivors:
        raise AssertionError(f"relay(s) {survivors} from {home} didn't stop")


def _cwd(pid: int) -> Path | None:
    """A process's working directory (Linux /proc, else lsof on macOS)."""
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        pass
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    paths = [line[1:] for line in out.splitlines() if line.startswith("n")]
    return Path(paths[0]).resolve() if paths else None
