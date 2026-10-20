"""`quorus replay <room>`: one self-contained page of how the agents did a task.

Slate item 7 (2026-10-17): people share what impresses them, and a room's
work was invisible outside the terminal. The replay is the conversation, the
commits it produced (with who made them and what changed) and the time from
the first human ask to the last publish, as one local HTML file. Nothing is
uploaded: the user decides whether to share it. Every body is escaped and
credentials are masked before they are written.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quorus.config import load_config
from quorus.ledger import read_provenance, vendor_of
from quorus.observability.redaction import env_secret_values, redact_secrets

MAX_BODY = 4000
REPO_URL = "https://github.com/Quorus-dev/Quorus"


@dataclass
class Commit:
    sha: str
    when: float
    subject: str
    author: str
    vendor: str
    stat: str


def _ts(value: Any) -> float | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def bound_repo(room: str) -> Path | None:
    from quorus.runtime.bindings import bindings_path

    try:
        data = json.loads(bindings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(room) if isinstance(data, dict) else None
    path = entry.get("path") if isinstance(entry, dict) else entry
    return Path(path).expanduser() if isinstance(path, str) and path else None


def commits_in(repo: Path, start: float, end: float) -> list[Commit]:
    """Commits on the repo's current branch made inside the window."""
    out = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "log", f"--since=@{int(start)}",
         f"--until=@{int(end) + 60}", "--format=%H%x09%ct%x09%an%x09%s", "HEAD"],
        cwd=repo, capture_output=True, text=True, timeout=30)
    rows: list[Commit] = []
    for line in out.stdout.splitlines():
        sha, ct, author, subject = (line.split("\t", 3) + ["", "", ""])[:4]
        meta = read_provenance(repo, sha) or {}
        agent = str(meta.get("agent") or author)
        stat = subprocess.run(["git", "show", "--shortstat", "--format=", sha], cwd=repo,
                              capture_output=True, text=True, timeout=30).stdout.strip()
        rows.append(Commit(sha[:8], float(ct or 0), subject, agent,
                           str(meta.get("vendor") or vendor_of(agent)), stat))
    return sorted(rows, key=lambda c: c.when)


def _vendor(name: str) -> str:
    v = vendor_of(name)
    return "human" if v == "unknown" else v


def _clean(text: str, known: list[str]) -> str:
    return html.escape(redact_secrets(text[:MAX_BODY], known))


def _mask(text: str, known: list[str]) -> str:
    """Every field written to the page is masked, not just message bodies:
    commit authors, stats and names are text anyone could have set."""
    return html.escape(redact_secrets(text, known))


def _clock(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _span(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds}s" if seconds < 90 else f"{seconds // 60}m {seconds % 60:02d}s"


def render(room: str, messages: list[dict[str, Any]], commits: list[Commit],
           known: list[str]) -> str:
    events: list[tuple[float, str]] = []
    agents: set[str] = set()
    humans: list[float] = []
    for m in messages:
        t = _ts(m.get("timestamp")) or 0.0
        who = str(m.get("from_name") or "?")
        body = str(m.get("content") or "")
        quiet = m.get("message_type") == "status" or body.startswith("(quorus)")
        vendor = _vendor(who)
        if vendor != "human":
            agents.add(who)
        elif not quiet:
            humans.append(t)
        kind = "quiet" if quiet else ("human" if vendor == "human" else "agent")
        events.append((t, f'<li class="msg {kind}"><div class="meta"><b>@{_mask(who, known)}'
                          f'</b><span class="tag">{_mask(vendor, known)}</span>'
                          f'<time>{_clock(t)}</time></div>'
                          f'<div class="body">{_clean(body, known)}</div></li>'))
    for c in commits:
        events.append((c.when, f'<li class="commit"><div class="meta"><b>commit '
                               f'{_mask(c.sha, known)}</b><span class="tag">'
                               f'@{_mask(c.author, known)} · {_mask(c.vendor, known)}</span>'
                               f'<time>{_clock(c.when)}</time></div><div class="body">'
                               f'{_clean(c.subject, known)}<small>{_mask(c.stat, known)}'
                               f'</small></div></li>'))
    events.sort(key=lambda e: e[0])
    first_ask = min(humans) if humans else (events[0][0] if events else 0.0)
    took = (commits[-1].when - first_ask) if commits else 0.0
    stats = [(str(len(agents)), "agents"), (str(len(messages)), "messages"),
             (str(len(commits)), "commits"),
             (_span(took) if commits else "-", "from first ask to last commit")]
    strip = "".join(f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for v, k in stats)
    vendors = sorted({_vendor(a) for a in agents})
    sub = (f"{len(agents)} agents on {', '.join(vendors)}" if agents else "no agents yet")
    return PAGE.format(room=_mask(room, known), sub=_mask(sub, known), strip=strip,
                       events="\n".join(e for _, e in events) or "<li>No messages.</li>",
                       made=time.strftime("%Y-%m-%d %H:%M"), repo=REPO_URL)


def cmd_replay(args: Any, console: Any) -> None:
    from quorus_cli.retro_cmd import _since
    from quorus_cli.scorecard_cmd import _history

    messages = _since(_history(args.room), args.since)
    if not messages:
        raise SystemExit(f"replay: no messages in #{args.room} in the last {args.since:g}h")
    times = [t for t in (_ts(m.get("timestamp")) for m in messages) if t]
    repo = Path(args.repo).expanduser() if args.repo else bound_repo(args.room)
    commits = commits_in(repo, min(times), max(times)) if repo and (repo / ".git").exists() \
        else []
    cfg = load_config()
    known = [v for v in (cfg.get("relay_secret"), cfg.get("api_key")) if v]
    known += env_secret_values(os.environ)
    out = Path(args.out or f"quorus-replay-{args.room}.html").expanduser().resolve()
    out.write_text(render(args.room, messages, commits, known), encoding="utf-8")
    console.print(f"[success]✓[/] Replay of #{args.room} ({len(messages)} messages, "
                  f"{len(commits)} commit{'' if len(commits) == 1 else 's'}): {out}")
    console.print("[dim]Nothing was uploaded. Credentials are masked, but read it "
                  "before you share it.[/]")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>#{room} replay</title>
<style>
:root{{--bg:#fafaf9;--card:#fff;--ink:#1c1917;--muted:#78716c;--line:#e7e5e4;
--accent:#4f46e5;--human:#eef2ff;--commit:#ecfdf5;--commit-ink:#065f46}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0c0a09;--card:#1c1917;--ink:#f5f5f4;
--muted:#a8a29e;--line:#292524;--accent:#a5b4fc;--human:#1e1b4b;--commit:#052e16;
--commit-ink:#86efac}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:28px 16px 48px}}
h1{{margin:0;font-size:24px}} .sub{{color:var(--muted);margin:4px 0 20px}}
.strip{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;
margin-bottom:24px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.stat b{{display:block;font-size:20px}} .stat span{{color:var(--muted);font-size:12px}}
ol{{list-style:none;margin:0;padding:0;display:grid;gap:8px}}
li{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
overflow-wrap:anywhere}}
li.human{{background:var(--human)}} li.commit{{background:var(--commit)}}
li.commit b{{color:var(--commit-ink);font-family:ui-monospace,Menlo,monospace}}
li.quiet{{background:transparent;border-style:dashed;color:var(--muted);font-size:13px}}
.meta{{display:flex;gap:8px;align-items:baseline;margin-bottom:4px;flex-wrap:wrap}}
.tag{{color:var(--muted);font-size:12px}} time{{margin-left:auto;color:var(--muted);font-size:12px}}
.body{{white-space:pre-wrap}} .body small{{display:block;color:var(--muted);margin-top:4px}}
footer{{margin-top:28px;color:var(--muted);font-size:13px}} a{{color:var(--accent)}}
</style></head><body><main>
<h1>#{room}</h1><p class="sub">{sub} · replay made {made}</p>
<section class="strip">{strip}</section>
<ol>
{events}
</ol>
<footer>Made with <a href="{repo}">Quorus</a>: one room for you and every agent.</footer>
</main></body></html>
"""
