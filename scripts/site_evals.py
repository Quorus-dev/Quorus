#!/usr/bin/env python3
"""Design loop, shadow mode: measure quorus.dev and the Quorum view for a release.

Arav's loop D (2026-10-18). Per release this records, never gates:

* the website: a clean ``npm run build``, Lighthouse (mobile and desktop) and
  full-page screenshots at 1440 and 390;
* the Quorum view: a throwaway relay on a free port with a per-run secret and a
  seeded demo room, a headed Chrome fps sample of ``quorus web --fps`` (a real
  display, since headless Chrome paces rAF at about 20 Hz) and a 4K still.

Only ``docs/evals/<label>/summary.json`` and ``summary.md`` are meant for git;
the PNG and Lighthouse JSON artefacts land in ``.evals/<label>/`` (ignored).
Thresholds (Lighthouse >= 99, fps >= 60, CLS 0) are written next to the numbers
but nothing fails on them: record-only for two releases, by Arav's rule.

Lighthouse and the fps capture are heavy jobs, so the whole run re-executes
itself under ``/usr/bin/lockf /tmp/quorus-full-suite.lock``, the lock the test
suite holds, and never runs beside a suite. Needs Chrome, Node 20+, and
Lighthouse resolvable with ``npx --no-install`` from ``website/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"
PROBE = ROOT / "scripts" / "site_evals" / "probe.mjs"
LOCK = "/tmp/quorus-full-suite.lock"
LOCK_ENV = "QUORUS_EVALS_LOCKED"
PYTHONPATH = ":".join(
    str(ROOT / p) for p in (".", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui")
)
THRESHOLDS = {"lighthouse_min": 99, "fps_min": 60, "cls_max": 0.0}
LH_CATEGORIES = "performance,accessibility,best-practices,seo"


def lock_command(argv: list[str], lock: str = LOCK, wait_s: int = 1800) -> list[str]:
    """The argv that runs ``argv`` holding the suite lock (same flags as quorus-suite)."""
    return ["/usr/bin/lockf", "-k", "-t", str(wait_s), lock, *argv]


def relock() -> None:
    """Re-exec under the suite lock once, so Lighthouse never runs beside a suite."""
    if os.environ.get(LOCK_ENV) == "1" or not Path("/usr/bin/lockf").exists():
        return
    env = dict(os.environ, **{LOCK_ENV: "1"})
    os.execve("/usr/bin/lockf", lock_command([sys.executable, *sys.argv]), env)


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    return m.group(1) if m else "unknown"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def wait_http(url: str, timeout_s: float = 20) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise RuntimeError(f"nothing answered at {url}")


# ---- the website ------------------------------------------------------------------


def build_site() -> Path:
    """A clean build; falls back to the pyproject version when PyPI is unreachable."""
    try:
        run(["npm", "run", "build"], cwd=WEBSITE)
    except subprocess.CalledProcessError:
        run(
            ["npm", "run", "build"],
            cwd=WEBSITE,
            env=dict(os.environ, QUORUS_SITE_VERSION=version()),
        )
    return WEBSITE / "dist"


def lighthouse(url: str, preset: str, out: Path) -> dict[str, Any]:
    flag = "--preset=desktop" if preset == "desktop" else "--form-factor=mobile"
    with tempfile.TemporaryDirectory(prefix="quorus-lh-") as prof:
        run(
            [
                "nice",
                "-n",
                "15",
                "npx",
                "--no-install",
                "lighthouse",
                url,
                flag,
                f"--only-categories={LH_CATEGORIES}",
                f"--chrome-flags=--headless=new --user-data-dir={prof}",
                "--output=json",
                f"--output-path={out}",
                "--quiet",
            ],
            cwd=WEBSITE,
        )
    return json.loads(out.read_text())


def shot(url: str, out: Path, w: int, h: int, dsf: int, *extra: str) -> dict[str, Any]:
    res = run(["node", str(PROBE), "shot", url, str(out), str(w), str(h), str(dsf), *extra])
    return json.loads(res.stdout.strip().splitlines()[-1])


# ---- the Quorum view ---------------------------------------------------------------

DEMO_MEMBERS = [
    ("maya", None),
    ("maya-claude", "claude-opus-5-5"),
    ("maya-codex", "gpt-5.5"),
    ("maya-gemini", "gemini-2.5-pro"),
    ("maya-opencode", "opencode/deepseek-v4.1-flash-free"),
]
DEMO_LINES = [
    (
        "maya",
        "@maya-claude split() crashes when people is 0; fix it with tests, "
        "then have maya-codex review it.",
    ),
    ("maya-claude", "On it. plan: guard people<1, round to cents, add tests."),
    (
        "maya-codex",
        "@maya-claude /claim review bcd8f10; checking split() callers "
        "and the zero-person contract.",
    ),
    ("maya-claude", "(quorus) published 1 commit(s) from quorus/maya-claude to main"),
    ("maya-codex", "@maya-claude ✅ /vote approve bcd8f10: no blocking findings; pytest 5/5."),
]


class Relay:
    """A throwaway relay for the demo scene: own port, own secret, own message file."""

    def __init__(self) -> None:
        self.port = free_port()
        self.secret = secrets.token_urlsafe(24)
        self.tmp = tempfile.TemporaryDirectory(prefix="quorus-evals-relay-")
        self.proc: subprocess.Popen[str] | None = None
        self.room_id = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Relay:
        env = dict(
            os.environ,
            RELAY_SECRET=self.secret,
            PORT=str(self.port),
            HOST="127.0.0.1",
            MESSAGES_FILE=str(Path(self.tmp.name) / "messages.json"),
            QUORUS_NOTIFY="0",
            LOG_LEVEL="WARNING",
            PYTHONPATH=PYTHONPATH,
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from quorus.relay import main; main()"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        wait_http(self.url + "/health")
        return self

    def __exit__(self, *exc: object) -> None:
        if self.proc is not None:  # stop by our own PID, never by pattern
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tmp.cleanup()

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def seed(self, room: str = "demo") -> None:
        """A room with five members, live presence (two busy, one asleep) and a few lines."""
        rooms = {r["name"]: r for r in self.api("GET", "/rooms")}
        self.room_id = (
            rooms.get(room) or self.api("POST", "/rooms", {"name": room, "created_by": "maya"})
        )["id"]
        for name, _model in DEMO_MEMBERS:
            self.api("POST", f"/rooms/{self.room_id}/join", {"participant": name, "role": "member"})
        now = time.time()
        beats = [
            ("maya-claude", "busy", {"activity": "running tests"}),
            ("maya-codex", "busy", {"activity": "reviewing bcd8f10"}),
            ("maya-gemini", "idle", {"waiting_on": "quota", "waiting_until": now + 1500}),
            ("maya-opencode", "idle", {}),
        ]
        for name, status, extra in beats:
            model = dict(DEMO_MEMBERS).get(name) or ""
            self.api(
                "POST",
                "/heartbeat",
                {"instance_name": name, "status": "idle", "room": room, "model": model},
            )
            self.api(
                "POST",
                "/heartbeat",
                {"instance_name": name, "status": status, "room": room, "model": model, **extra},
            )
        for who, text in DEMO_LINES:
            self.api("POST", f"/rooms/{self.room_id}/messages", {"from_name": who, "content": text})

    def quorum_url(self, fps: bool) -> str:
        return f"{self.url}/#token={self.secret}&name=maya&room=demo&view=quorum" + (
            "&fps=1" if fps else ""
        )


def fps_sample(url: str, seconds: int) -> dict[str, Any]:
    res = run(["node", str(PROBE), "fps", url, str(seconds), "1440", "900", "2"])
    return json.loads(res.stdout.strip().splitlines()[-1])


# ---- the summary -------------------------------------------------------------------


def lh_numbers(report: dict[str, Any]) -> dict[str, Any]:
    cats = report.get("categories", {})
    audits = report.get("audits", {})

    def metric(key: str) -> float | None:
        v = audits.get(key, {}).get("numericValue")
        return round(float(v), 3) if v is not None else None

    return {
        "scores": {k: round(float(v.get("score") or 0) * 100) for k, v in cats.items()},
        "lcp_ms": metric("largest-contentful-paint"),
        "cls": metric("cumulative-layout-shift"),
        "tbt_ms": metric("total-blocking-time"),
        "fcp_ms": metric("first-contentful-paint"),
    }


def summarize(
    label: str,
    lh: dict[str, dict[str, Any]],
    fps: dict[str, Any] | None,
    shots: dict[str, Any],
    sizes: dict[str, int],
) -> dict[str, Any]:
    """The one record per release. Record-only: thresholds sit beside the numbers."""
    out: dict[str, Any] = {
        "label": label,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "shadow",
        "thresholds": THRESHOLDS,
        "site": {k: lh_numbers(v) for k, v in lh.items()},
        "shots": shots,
        "sizes_bytes": sizes,
    }
    if fps:
        out["quorum"] = {
            k: fps.get(k)
            for k in (
                "renderer",
                "min",
                "median",
                "max",
                "dpr",
                "quality",
                "width",
                "height",
                "tags",
            )
        }
        out["quorum"]["samples"] = fps.get("samples", [])
    return out


def fmt(v: Any) -> str:
    """Whole numbers without a trailing .0; everything else as is."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def markdown(s: dict[str, Any]) -> str:
    lines = [
        f"# Design evals: {s['label']}",
        "",
        f"Recorded {s['recorded_at']} in {s['mode']} mode (record-only).",
        "",
    ]
    for preset, n in s.get("site", {}).items():
        sc = n["scores"]
        lines.append(
            f"- Lighthouse {preset}: "
            + " / ".join(f"{k} {v}" for k, v in sc.items())
            + f"; LCP {fmt(n['lcp_ms'])} ms, CLS {fmt(n['cls'])}, TBT {fmt(n['tbt_ms'])} ms"
        )
    q = s.get("quorum")
    if q:
        lines.append(
            f"- Quorum fps (headed): min {q['min']}, median {q['median']}, max {q['max']}; "
            f"DPR {q['dpr']}, quality {q['quality']}, "
            f"canvas {q['width']}x{q['height']}, seats {q['tags']}"
        )
        lines.append(f"- Renderer: {q['renderer']}")
    th = s["thresholds"]
    lines += [
        "",
        f"Thresholds for later gating: Lighthouse >= {th['lighthouse_min']}, "
        f"fps >= {th['fps_min']}, "
        f"CLS <= {th['cls_max']}. Nothing fails on them yet.",
        "",
    ]
    return "\n".join(lines)


# ---- main ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--label",
        default=version(),
        help="folder name under docs/evals (default: pyproject version)",
    )
    ap.add_argument("--out", default=str(ROOT / "docs" / "evals"))
    ap.add_argument("--artifacts", default=str(ROOT / ".evals"))
    ap.add_argument("--skip-site", action="store_true")
    ap.add_argument("--skip-quorum", action="store_true")
    ap.add_argument("--fps-seconds", type=int, default=20)
    ap.add_argument("--pages", default="index",
                    help="comma list of pages to audit, e.g. index,install")
    ap.add_argument(
        "--no-lock", action="store_true", help="don't re-exec under the suite lock (tests)"
    )
    args = ap.parse_args(argv)
    if not args.no_lock:
        relock()

    label = args.label
    art = Path(args.artifacts) / label
    art.mkdir(parents=True, exist_ok=True)
    lh: dict[str, dict[str, Any]] = {}
    shots: dict[str, Any] = {}
    sizes: dict[str, int] = {}
    fps: dict[str, Any] | None = None

    if not args.skip_site:
        dist = build_site()
        sizes = {
            p.name: p.stat().st_size for p in sorted((dist / "assets").glob("*")) if p.is_file()
        }
        port = free_port()
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "-d",
                str(dist),
                "--bind",
                "127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = f"http://127.0.0.1:{port}/"
            wait_http(url)
            for page in [p.strip() for p in args.pages.split(",") if p.strip()]:
                page_url = url if page == "index" else f"{url}{page}.html"
                for preset in ("mobile", "desktop"):
                    key = preset if page == "index" else f"{page} {preset}"
                    lh[key] = lighthouse(page_url, preset, art / f"lighthouse-{page}-{preset}.json")
            shots["site-1440"] = shot(url, art / "site-1440.png", 1440, 2600, 1, "--settle", "6000")
            shots["site-390"] = shot(url, art / "site-390.png", 390, 3200, 2, "--settle", "4000")
        finally:
            server.terminate()
            server.wait(timeout=10)

    if not args.skip_quorum:
        with Relay() as relay:
            relay.seed()
            fps = fps_sample(relay.quorum_url(fps=True), args.fps_seconds)
            shots["quorum-4k"] = shot(
                relay.quorum_url(fps=False),
                art / "quorum-4k.png",
                1920,
                1080,
                2,
                "--settle",
                "18000",
                "--lock",
            )
            shots["quorum-reduced"] = shot(
                relay.quorum_url(fps=False),
                art / "quorum-reduced.png",
                1440,
                900,
                1,
                "--settle",
                "10000",
                "--lock",
                "--reduce",
            )

    summary = summarize(label, lh, fps, shots, sizes)
    out = Path(args.out) / label
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "summary.md").write_text(markdown(summary))
    print(markdown(summary))
    print(f"artefacts: {art}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
