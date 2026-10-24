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

from quorus.board import BOARD_JS, ROWS_JS
from quorus.dashboard import DASHBOARD_HTML


def test_board_is_served_same_origin_and_safe() -> None:
    from quorus.relay import app

    with TestClient(app) as c:
        js = c.get("/board.js")
        page = c.get("/")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert '<script type="module" src="/board.js"></script>' in page.text  # ESM, slice 2
    assert "script-src 'self'" in page.headers.get("content-security-policy", "")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "dangerouslySetInnerHTML"):
        assert sink not in BOARD_JS and sink not in ROWS_JS, sink
    assert not re.search(r'"/(rooms|v1/approvals)/"\s*\+\s*(?!\s|enc\()', BOARD_JS + ROWS_JS)
    assert 'id="viewToggle"' in DASHBOARD_HTML and 'id="board"' in DASHBOARD_HTML


def test_approve_buttons_need_the_viewers_own_key() -> None:
    # The shared secret never decides an approval (team decision, 2026-10-17).
    assert 'token().split(".").length === 3' in ROWS_JS
    assert "if (!ownKey())" in BOARD_JS and '"quorus approve "' in BOARD_JS
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
    (tmp_path / "rows.js").write_text(ROWS_JS)
    script.write_text(
        "const vals = { qn: 'arav', qt: 'secret' };\n"
        "globalThis.sessionStorage = { getItem: (k) => vals[k] || null };\n"
        "const b = require('./rows.js');\n"
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
    assert web_url("http://r", "a.b.c", "arav", None, True, fps=True).endswith("&view=board&fps=1")
    from quorus.dashboard import DASHBOARD_JS as _djs

    assert 'if (frag.get("fps")) sessionStorage.setItem("qfps", "1");' in _djs
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



def test_mission_control_keeps_chat_beside_the_panels_on_wide_screens() -> None:
    """Watch every agent work and talk to them at once: on a wide screen the
    Board view no longer hides the chat; a phone still toggles between them."""
    assert '$(id).hidden = on && !wide' in BOARD_JS
    assert '$("chat").classList.toggle("split", on && wide)' in BOARD_JS
    assert "#chat.split{display:grid" in DASHBOARD_HTML
    assert 'grid-template-areas:"bar bar" "board msgs"' in DASHBOARD_HTML


def test_list_is_the_default_board_mode() -> None:
    """The Quorum table opens only on request (#view=quorum or its button)."""
    assert '<button id="modeList" class="on" type="button">List</button>' in DASHBOARD_HTML
    assert '<div id="quorum" hidden>' in DASHBOARD_HTML
    assert "localStorage" not in BOARD_JS  # the mode is never remembered across visits
    assert 'if (view === "quorum") mode("quorum");' in BOARD_JS


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_a_step_older_than_ten_minutes_clears(tmp_path: Path) -> None:
    """A daemon stuck in a wake showed "running tests · 7h 26m" for hours
    (Arav, 2026-10-18). The relay's last_wake_at says when the wake began."""
    now = 1792260000
    presence = {
        "stale-claude": {"name": "stale-claude", "online": True, "status": "busy",
                         "activity": "running tests", "last_wake_at": now - 7 * 3600,
                         "trail": [{"t": now - 7 * 3600, "step": "running tests"}]},
        "fresh-claude": {"name": "fresh-claude", "online": True, "status": "busy",
                         "activity": "editing relay.py",
                         "last_wake_at": "2026-10-17T12:00:00+00:00"},
        "no-since-codex": {"name": "no-since-codex", "online": True, "status": "busy",
                           "activity": "reviewing"},
    }
    iso_now = 1792260000  # 2026-10-17T12:00:00Z is 1760702400; use the number form too
    script = tmp_path / "run.js"
    (tmp_path / "rows.js").write_text(ROWS_JS)
    script.write_text(
        "globalThis.sessionStorage = { getItem: () => null };\n"
        "const b = require('./rows.js');\n"
        f"const pres = {json.dumps(presence)};\n"
        f"const now = {iso_now};\n"
        "pres['fresh-claude'].last_wake_at = new Date((now - 120) * 1000).toISOString();\n"
        "const rows = Object.keys(pres).map((n) => b.rowState(n, pres, [], [], now));\n"
        "console.log(JSON.stringify(rows));\n")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    rows = {r["name"]: r for r in json.loads(out.stdout)}
    assert rows["stale-claude"]["rank"] == 5 and rows["stale-claude"]["line"] == "idle"
    assert rows["stale-claude"]["trail"] == []
    fresh = rows["fresh-claude"]
    assert fresh["rank"] == 4 and fresh["line"] == "editing relay.py · 2m"
    # No relay timestamp: the page's own first sighting counts, so it stays working.
    assert rows["no-since-codex"]["rank"] == 4
    assert rows["no-since-codex"]["line"].startswith("reviewing")
    from quorus.dashboard import DASHBOARD_JS

    # The chat's working line applies the same ten minutes.
    assert "last_wake_at" in DASHBOARD_JS and "< 600000" in DASHBOARD_JS


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_newest_tuning_line_becomes_a_board_row(tmp_path: Path) -> None:
    """L2 (0.5.3): the daemon's adaptation line is a row on the agent it names,
    shadow or applied, with its undo command. Every self-tuning slice shares
    this one affordance instead of adding screens."""
    now = 1792260000
    presence = {"x-gemini": {"name": "x-gemini", "online": True, "status": "active"},
                "x-gemini-2": {"name": "x-gemini-2", "online": True, "status": "active"}}
    shadow = {"from_name": "x-gemini", "message_type": "status",
              "timestamp": "2026-10-18T08:00:00Z",
              "content": "(quorus) t1 (shadow, not applied): would set @open bids x0.75 for "
                         "@x-gemini · x0.75 · 2 errors, 0 clean/24h · undo: quorus tune undo t1"}
    applied = {"from_name": "x-gemini", "message_type": "status",
               "timestamp": "2026-10-18T08:05:00Z",
               "content": "(quorus) adapted arav.t2: @open bids x0.60 for @x-gemini · 3 errors, "
                          "0 clean/24h · undo: quorus tune undo arav.t2"}  # owner-prefixed id
    # Neither of these may land on a row: a line about x-gemini posted by someone
    # else, and a plain chat message from x-gemini (its daemon posts `status`).
    spoof = dict(applied, from_name="x-gemini-2", timestamp="2026-10-18T08:09:00Z",
                 content=applied["content"].replace("t2", "t9"))
    chat = dict(applied, message_type="chat", timestamp="2026-10-18T08:09:30Z",
                content=applied["content"].replace("t2", "t8"))
    script = tmp_path / "run.js"
    (tmp_path / "rows.js").write_text(ROWS_JS)
    script.write_text(
        "globalThis.sessionStorage = { getItem: () => null };\n"
        "const b = require('./rows.js');\n"
        f"const pres = {json.dumps(presence)}, shadow = {json.dumps(shadow)};\n"
        f"const applied = {json.dumps(applied)}, now = {now};\n"
        f"const spoof = {json.dumps(spoof)}, chat = {json.dumps(chat)};\n"
        "const run = (hist) => Object.fromEntries(Object.keys(pres)"
        ".map((n) => [n, b.rowState(n, pres, hist, [], now).tuning || null]));\n"
        "console.log(JSON.stringify({ both: run([shadow, applied, spoof, chat]), "
        "one: run([shadow]) }));\n")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["both"]["x-gemini"] == {  # the newest line wins
        "id": "arav.t2", "shadow": False, "undo": "quorus tune undo arav.t2",
        "text": "adapted: @open bids x0.60 for @x-gemini · 3 errors, 0 clean/24h"}
    assert got["one"]["x-gemini"] == {
        "id": "t1", "shadow": True, "undo": "quorus tune undo t1",
        "text": "shadow, not applied: would set @open bids x0.75 for @x-gemini · x0.75 · "
                "2 errors, 0 clean/24h"}
    assert got["both"]["x-gemini-2"] is None  # "@x-gemini" is not "@x-gemini-2"
    assert 'mine.slice().reverse().find((m) => m.message_type === "status"' in ROWS_JS
    assert '<div class="btune">${r.tuning.text + " · undo: "}' in BOARD_JS
    assert ".btune code{" in DASHBOARD_HTML


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_track_owner_preserves_dashed_human_names(tmp_path):
    (tmp_path / "rows.js").write_text(ROWS_JS)
    script = tmp_path / "owner.js"
    script.write_text(
        "const {trackOwner} = require('./rows.js');\n"
        "for (const name of ['alice-smith', 'alice-smith-codex', 'alice-smith-codex-desktop']) "
        "{ if (trackOwner(name) !== 'alice-smith') throw new Error(name); }\n")
    subprocess.run([NODE, str(script)], check=True, capture_output=True)


def test_the_scripts_are_files_not_python_strings() -> None:
    """board-inbox.md slice 1: the JS (and the page's CSS) live in quorus/web so ESLint
    and node see files. The modules only load them; what the relay serves is unchanged."""
    from quorus import board, dashboard

    web = Path(board.__file__).parent / "web"
    assert BOARD_JS == "\n" + (web / "board.js").read_text(encoding="utf-8")
    assert dashboard.DASHBOARD_JS == "\n" + (web / "web.js").read_text(encoding="utf-8")
    assert (web / "web.css").read_text(encoding="utf-8").rstrip("\n") in DASHBOARD_HTML
    for module in (board, dashboard):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "function " not in source and "{display:" not in source, module.__name__
        assert source.count("\n") < 200, module.__name__
    assert (web / "eslint.config.mjs").exists()


VENDOR_SHA256 = {  # quorus/web/vendor/README.txt: a changed byte fails here first
    "preact.module.js": "4d2617f3f082550223d37f781114b5f723362fc4a337459715a7083664dc10c1",
    "htm.module.js": "ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4",
}


def test_vendored_preact_and_htm_are_pinned_and_served() -> None:
    """board-inbox.md slice 2: Preact 11.0.2 + htm 3.1.1 as ESM, same origin, no eval."""
    import hashlib

    from quorus import quorum_view
    from quorus.relay import app

    web = Path(quorum_view.__file__).parent / "web"
    for name, digest in VENDOR_SHA256.items():
        data = (web / "vendor" / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        assert b"eval(" not in data and b"Function(" not in data, name
    assert "MIT License" in (web / "vendor" / "PREACT-LICENSE.txt").read_text()
    assert "Apache License" in (web / "vendor" / "HTM-LICENSE.txt").read_text()
    for src in (BOARD_JS, ROWS_JS):  # same-origin imports only
        assert not re.search(r"https?://", src)
    for dep in ('from "/vendor/preact.module.js"', 'from "/vendor/htm.module.js"',
                'from "/rows.js"'):
        assert dep in BOARD_JS, dep
    with TestClient(app) as c:
        for name in VENDOR_SHA256:
            r = c.get("/vendor/" + name)
            assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
            assert "max-age" in r.headers["cache-control"]
        rows = c.get("/rows.js")
        assert rows.status_code == 200 and rows.text == ROWS_JS
        assert rows.headers["cache-control"] == "no-cache"
        for bad in ("/vendor/PREACT-LICENSE.txt", "/vendor/HTM-LICENSE.txt"):
            assert c.get(bad).status_code == 404, bad
