"""The "needs you" board (2026-10-17): served under the CSP, never trusts data
as HTML, and sorts agents by what needs a human. The row logic runs under
node against the shapes the relay really returns."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quorus.board import BOARD_JS
from quorus.dashboard import DASHBOARD_HTML


def test_board_is_served_same_origin_and_safe() -> None:
    from quorus.relay import app

    with TestClient(app) as c:
        js = c.get("/board.js")
        page = c.get("/")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert '<script src="/board.js"></script>' in page.text
    assert "script-src 'self'" in page.headers.get("content-security-policy", "")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in BOARD_JS, sink
    assert not re.search(r'"/(rooms|v1/approvals)/"\s*\+\s*(?!\s|enc\()', BOARD_JS)
    assert 'id="viewToggle"' in DASHBOARD_HTML and 'id="board"' in DASHBOARD_HTML


def test_approve_buttons_need_the_viewers_own_key() -> None:
    # The shared secret never decides an approval (team decision, 2026-10-17).
    assert 'token().split(".").length === 3' in BOARD_JS
    assert "if (ownKey())" in BOARD_JS and '"quorus approve "' in BOARD_JS
    assert "decided_by" not in BOARD_JS  # the relay names the decider from the JWT


def test_board_stops_polling_while_the_tab_is_hidden() -> None:
    # A phone in a pocket kept 3 requests every 3s going (review of 9cb91702).
    assert "document.hidden" in BOARD_JS and '"visibilitychange"' in BOARD_JS


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_rows_sort_by_what_needs_a_human(tmp_path: Path) -> None:
    now = 1792260000
    presence = {
        "a-claude": {"name": "a-claude", "online": True, "status": "busy",
                     "activity": "editing relay.py", "room": "r",
                     "model": "claude-opus-5-5",
                     "trail": [{"t": now - 60 * i, "step": f"step {i}"} for i in range(7, 0, -1)]},
        "a-codex": {"name": "a-codex", "online": True, "status": "active",
                    "waiting_on": "pacing", "waiting_until": now + 840},
        "a-gemini": {"name": "a-gemini", "online": True, "status": "active",
                     "waiting_on": "stuck"},
        "a-opencode": {"name": "a-opencode", "online": True, "status": "active",
                       "waiting_on": "quota", "waiting_until": now + 3600},
        "b-claude": {"name": "b-claude", "online": False, "status": "offline"},
    }
    iso = lambda t: f"2026-10-17T{t}Z"  # noqa: E731
    history = [
        {"from_name": "a-gemini", "timestamp": iso("12:00:00"),
         "content": "⚠ ran out of time before finishing its reply."},
        {"from_name": "a-claude", "timestamp": iso("12:01:00"),
         "content": "✅ done\n(quorus) published 2 commit(s) from quorus/a-claude to main"},
        {"from_name": "a-codex", "timestamp": iso("12:02:00"),
         "content": "@a-claude /disagree blocking on abc1234: redirects forward the key"},
        {"from_name": "b-claude", "timestamp": iso("12:03:00"),
         "content": "@arav should I take the legal memo next?"},
    ]
    approvals = [{"id": "ap1", "agent": "a-codex", "tool_name": "Bash",
                  "input_preview": "git push"}]
    script = tmp_path / "run.js"
    (tmp_path / "board.js").write_text(BOARD_JS)
    script.write_text(
        "const vals = { qn: 'arav', qt: 'secret' };\n"
        "globalThis.sessionStorage = { getItem: (k) => vals[k] || null };\n"
        "const b = require('./board.js');\n"
        f"const pres = {json.dumps(presence)}, hist = {json.dumps(history)};\n"
        f"const appr = {json.dumps(approvals)}, now = {now};\n"
        "const rows = Object.keys(pres).map((n) => b.rowState(n, pres, hist, appr, now));\n"
        "console.log(JSON.stringify(rows));\n")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    rows = {r["name"]: r for r in json.loads(out.stdout)}
    assert rows["a-codex"]["rank"] == 1 and rows["a-codex"]["approvals"][0]["id"] == "ap1"
    assert rows["b-claude"]["rank"] == 1 and "legal memo" in rows["b-claude"]["asks"][0]
    assert rows["a-gemini"]["rank"] == 2 and rows["a-gemini"]["line"].startswith("stuck")
    assert rows["a-opencode"]["line"].startswith("out of quota")
    assert rows["a-claude"]["line"].startswith("editing relay.py")
    assert rows["a-claude"]["review"] == "blocked by @a-codex"
    assert rows["a-claude"]["shipped"].startswith("shipped 2 commit(s) to main")
    assert rows["a-claude"]["model"] == "claude-opus-5-5" and rows["a-codex"]["model"] == ""
    # Mission control: the last 5 live steps, oldest first; offline agents show none.
    assert [t["step"] for t in rows["a-claude"]["trail"]] == [f"step {i}" for i in range(5, 0, -1)]
    assert rows["b-claude"]["trail"] == [] and rows["a-codex"]["trail"] == []
    assert 'api("/presence?trail=1")' in BOARD_JS and '"btrail"' in BOARD_JS


def test_quorus_web_link_lands_on_the_board_signed_in_as_you() -> None:
    from quorus_cli.web_cmd import web_url

    url = web_url("http://127.0.0.1:8787", "a.b.c", "arav", "weekend-quorus", True)
    assert url == ("http://127.0.0.1:8787/#token=a.b.c&name=arav&room=weekend-quorus"
                   "&view=board")
    assert "?" not in url  # the credential only ever rides in the fragment
    from quorus.dashboard import DASHBOARD_JS

    assert 'sessionStorage.setItem("qr"' in DASHBOARD_JS
    assert 'view === "board" || view === "quorum"' in BOARD_JS  # #view=quorum opens the table


def test_board_never_waits_for_the_scorecard() -> None:
    """Live: the first scorecard took many seconds and the board stayed blank."""
    i = BOARD_JS.index('"/scorecard")')
    window = BOARD_JS[BOARD_JS.rindex("function refresh()", 0, i):i + 400]
    assert "return members.then(() => Promise.all([" in BOARD_JS
    assert "Promise.all([members, card])" not in BOARD_JS  # rows don't wait for it
    assert "refresh();" in window  # it re-renders once the track records arrive
    assert "Loading the team" in BOARD_JS
