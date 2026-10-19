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


def child_env(home: Path, relay_url: str) -> dict[str, str]:
    # Absolute: the child runs in the tmp HOME, where a relative entry like "."
    # would load some other copy of quorus than the one under test.
    code = [str(Path(p).resolve()) for p in sys.path if p and Path(p).is_dir()]
    dead = "http://127.0.0.1:9"
    return {
        "HOME": str(home), "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONPATH": os.pathsep.join(code), "PYTHONDONTWRITEBYTECODE": "1",
        "TERM": "xterm-256color", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
        "COLUMNS": "120", "LINES": "40", "RELAY_URL": relay_url, "QUORUS_NO_LAUNCHD": "1",
        "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead,
        "http_proxy": dead, "https_proxy": dead, "all_proxy": dead,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }


class PtySession:
    def __init__(self, home: Path, *, cols: int = 120, rows: int = 40) -> None:
        self.home = home
        self.port = free_port()
        self.relay_url = f"http://127.0.0.1:{self.port}"
        self._raw = ""
        self._mark = 0
        self._lock = threading.Lock()
        home.mkdir(parents=True, exist_ok=True)
        pid, fd = pty.fork()
        if pid == 0:  # the child: the user's terminal is this pty
            try:
                os.chdir(home)
                os.execve(sys.executable, [sys.executable, "-c", _RUN],
                          child_env(home, self.relay_url))
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
        self._stop_relay()

    def _stop_relay(self) -> None:
        """The relay the first run started (detached, its own session): stopped
        through local_relay, which signals only the process its pid file names."""
        from quorus.runtime.local_relay import stop_started_relay

        saved = os.environ.get("QUORUS_CONFIG_DIR")
        os.environ["QUORUS_CONFIG_DIR"] = str(self.home / ".quorus")
        try:
            stop_started_relay(self.port)
        finally:
            if saved is None:
                os.environ.pop("QUORUS_CONFIG_DIR", None)
            else:
                os.environ["QUORUS_CONFIG_DIR"] = saved
