"""TUI surface for pending approvals (WAKE_REBUILD L3).

A blocked agent burns its wake budget while it waits, so this panel is
loud by default — unconditional when anything is pending — and answerable
with /approve and /deny without leaving the chat pane.
"""

import time

from quorus_tui.approvals_panel import (
    MAX_VISIBLE_ROWS,
    render_approvals_panel,
)


def _rec(**kw):
    base = {
        "id": "apr_abc123",
        "agent": "arav-claude",
        "tool_name": "Bash",
        "input_preview": "pytest -q",
        "status": "pending",
        "expires_at": time.time() + 300,
    }
    base.update(kw)
    return base


def _text(rows):
    return "\n".join(r.plain for r in rows)


def test_empty_when_nothing_pending():
    assert render_approvals_panel([]) == []


def test_settled_requests_are_not_shown():
    assert render_approvals_panel([_rec(status="approved")]) == []
    assert render_approvals_panel([_rec(status="denied")]) == []


def test_shows_agent_tool_and_unblock_commands():
    out = _text(render_approvals_panel([_rec()]))
    assert "Waiting on you" in out
    assert "arav-claude" in out and "Bash" in out
    assert "pytest -q" in out
    assert "/approve apr_abc123" in out and "/deny apr_abc123" in out


def test_countdown_is_urgent_near_expiry():
    soon = _text(render_approvals_panel([_rec(expires_at=time.time() + 20)]))
    assert "20s left" in soon or "19s left" in soon
    later = _text(render_approvals_panel([_rec(expires_at=time.time() + 300)]))
    assert "m left" in later
    dead = _text(render_approvals_panel([_rec(expires_at=time.time() - 5)]))
    assert "expired" in dead


def test_long_input_is_truncated_not_wrapped():
    out = _text(render_approvals_panel([_rec(input_preview="y" * 500)],
                                       console_width=80))
    assert "…" in out
    assert all(len(line) <= 120 for line in out.split("\n"))


def test_overflow_points_at_the_cli():
    many = [_rec(id=f"apr_{i}") for i in range(MAX_VISIBLE_ROWS + 3)]
    out = _text(render_approvals_panel(many))
    assert "+3 more" in out and "quorus approvals" in out


def test_missing_fields_never_crash():
    out = _text(render_approvals_panel([{"status": "pending"}]))
    assert "Waiting on you" in out


# ── slash-command flow ──────────────────────────────────────────────────────

def test_slash_approve_registered_and_decides(monkeypatch):
    """/approve <id> answers the relay and drops the row locally."""
    from quorus_tui import hub

    assert "/approve" in hub.SLASH_COMMANDS
    assert "/deny" in hub.SLASH_COMMANDS

    calls = []

    def fake_decide(relay, secret, approval_id, approve):
        calls.append((approval_id, approve))
        return True, f"{approval_id} → approved"

    monkeypatch.setattr(hub, "_decide_approval", fake_decide)
    state = hub.HubState()
    state.set_pending_approvals([_rec(id="apr_1"), _rec(id="apr_2")])

    handler = hub.SLASH_COMMANDS["/approve"][1]
    assert handler("apr_1", state, "http://relay", "s", "arav", None) is True
    assert calls == [("apr_1", True)]
    assert [r["id"] for r in state.get_pending_approvals()] == ["apr_2"]


def test_slash_approve_without_id_is_unambiguous_only_when_one_pending(
    monkeypatch,
):
    from quorus_tui import hub

    calls = []
    monkeypatch.setattr(
        hub, "_decide_approval",
        lambda r, s, i, a: (calls.append(i), (True, "ok"))[1],
    )
    state = hub.HubState()

    # Two pending → refuse to guess.
    state.set_pending_approvals([_rec(id="apr_1"), _rec(id="apr_2")])
    hub.SLASH_COMMANDS["/approve"][1]("", state, "u", "s", "a", None)
    assert calls == []

    # Exactly one → "/approve" alone means that one.
    state.set_pending_approvals([_rec(id="apr_solo")])
    hub.SLASH_COMMANDS["/approve"][1]("", state, "u", "s", "a", None)
    assert calls == ["apr_solo"]


def test_slash_deny_reports_relay_failure(monkeypatch):
    from quorus_tui import hub

    monkeypatch.setattr(
        hub, "_decide_approval",
        lambda *a, **k: (False, "an agent cannot decide its own approval"),
    )
    state = hub.HubState()
    state.set_pending_approvals([_rec(id="apr_1")])
    hub.SLASH_COMMANDS["/deny"][1]("apr_1", state, "u", "s", "a", None)
    # Failure keeps the row — nothing was actually decided.
    assert [r["id"] for r in state.get_pending_approvals()] == ["apr_1"]


def test_approvals_staleness_window_is_tight():
    """Approvals expire, so the panel refreshes far more eagerly than the
    30s work-queue window."""
    from quorus_tui import hub

    state = hub.HubState()
    assert state.approvals_stale() is True
    state.set_pending_approvals([])
    assert state.approvals_stale() is False
    assert state.approvals_stale(ttl=-1) is True
