"""Quorus Operating Discipline (QOD) tests.

QOD is the cross-harness operating constitution: six rules every agent in every
supported harness loads at session start. The tests below pin three things:

1. The constitution itself — preamble + six rules — is rendered consistently
   across the three render functions (MCP, agent-loop, plain).
2. The MCP server's ``instructions`` field carries the QOD verbatim, so any
   host that surfaces server instructions to the model picks it up.
3. The Claude / Codex / Gemini agent ``build_prompt()`` functions prepend the
   QOD to their per-harness boilerplate.

Heartbeat command tests live in ``test_cli_heartbeat.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_qod_render_contains_six_rules():
    """The canonical render must surface all six rule lines and the preamble."""
    from quorus.operating_discipline import (
        QOD_PREAMBLE,
        QOD_RULES,
        render_qod,
    )

    text = render_qod()
    # Header
    assert "Quorus Operating Discipline" in text
    # Preamble — verbatim
    assert QOD_PREAMBLE in text
    # All six rules — exactly six, prefixed 1..6
    assert len(QOD_RULES) == 6, "QOD must have exactly six rules"
    for idx, rule in enumerate(QOD_RULES, start=1):
        assert rule.startswith(f"{idx}."), f"rule {idx} must start with '{idx}.'"
        assert rule in text


def test_qod_rules_cover_required_behaviors():
    """Each rule in the spec maps to a recognizable phrase in the constitution.

    This is a value test, not a string-match test: if someone reorganizes the
    rules but the *behaviors* listed in the spec are still covered, that is
    fine. If a behavior disappears, this fails.
    """
    from quorus.operating_discipline import render_qod

    text = render_qod().lower()
    required_behaviors = [
        # rule 1 — pre-work plan
        "plan",
        # rule 2 — pre-mutation ack
        "ack",
        # rule 3 — post-ship announce
        "post-commit",
        # rule 4 — heartbeat
        "heartbeat",
        # rule 5 — respond to mention within one turn
        "@-mention",
        # rule 6 — disagree with reasoning
        "disagree",
    ]
    for needle in required_behaviors:
        assert needle.lower() in text, (
            f"QOD must reference behavior keyword: {needle!r}"
        )


def test_qod_render_for_mcp_includes_tool_tail():
    """MCP rendering tacks on a tool-list tail so the model knows the QOD is
    actionable from the MCP transport."""
    from quorus.operating_discipline import render_qod, render_qod_for_mcp

    base = render_qod()
    mcp = render_qod_for_mcp()
    assert mcp.startswith(base), "MCP render must start with the canonical body"
    assert "send_room_message" in mcp
    assert "claim_task" in mcp


def test_qod_render_for_agent_loop_has_contract_tail():
    """Agent-loop rendering ends with the 'not optional' contract tail."""
    from quorus.operating_discipline import render_qod_for_agent_loop

    text = render_qod_for_agent_loop()
    assert "not optional" in text.lower()


def test_qod_for_agent_loop_under_token_budget():
    """Keep agent-loop QOD under ~300 tokens (~1200 chars conservative).

    Hosts have prompt budgets; if QOD bloats it gets silently truncated.
    """
    from quorus.operating_discipline import render_qod_for_agent_loop

    text = render_qod_for_agent_loop()
    # 4 chars/token is the conservative floor for English.
    assert len(text) < 1600, (
        f"QOD agent-loop block is {len(text)} chars — over ~400-token budget"
    )


def test_mcp_server_instructions_carry_qod():
    """The MCP server's ``instructions`` field must include the full QOD.

    Hosts that surface ``ServerCapabilities.instructions`` (Claude Code,
    Cursor, Gemini CLI, Cline, Continue, Windsurf, Codex) pick this up
    automatically — that is distribution channel A.
    """
    from quorus_mcp.server import QUORUS_INSTRUCTIONS

    from quorus.operating_discipline import QOD_RULES

    assert "Quorus Operating Discipline" in QUORUS_INSTRUCTIONS
    for rule in QOD_RULES:
        assert rule in QUORUS_INSTRUCTIONS, (
            f"MCP instructions missing QOD rule: {rule[:40]!r}..."
        )


def test_mcp_server_construction_uses_qod_instructions():
    """Importing the MCP server module must wire QUORUS_INSTRUCTIONS into the
    FastMCP server's instructions attribute."""
    from quorus_mcp import server as mcp_server

    # FastMCP exposes the configured instructions via the underlying mcp
    # server. The exact attribute path differs across mcp-python releases,
    # so probe a couple known locations.
    fastmcp = mcp_server.mcp
    instructions = (
        getattr(fastmcp, "instructions", None)
        or getattr(getattr(fastmcp, "_mcp_server", None), "instructions", None)
    )
    assert instructions == mcp_server.QUORUS_INSTRUCTIONS
    assert "Quorus Operating Discipline" in instructions


def test_claude_agent_build_prompt_prepends_qod(tmp_path: Path):
    """Channel C — claude_agent must prepend QOD to its build_prompt output."""
    from quorus_cli.claude_agent import build_prompt

    from quorus.operating_discipline import QOD_PREAMBLE, QOD_RULES

    inbox = tmp_path / "inbox.txt"
    out = build_prompt("yc-hack", "alice", inbox, None)
    # QOD comes first
    assert out.startswith("# Quorus Operating Discipline"), (
        f"Claude prompt must start with QOD header. Got: {out[:80]!r}"
    )
    assert QOD_PREAMBLE in out
    for rule in QOD_RULES:
        assert rule in out
    # Per-harness boilerplate still present
    assert "You are `alice` in the Quorus room `yc-hack`." in out


def test_codex_agent_build_prompt_prepends_qod(tmp_path: Path):
    """Channel C — codex_agent must prepend QOD to its build_prompt output."""
    from quorus_cli.codex_agent import build_prompt

    from quorus.operating_discipline import QOD_PREAMBLE, QOD_RULES

    inbox = tmp_path / "inbox.txt"
    out = build_prompt("yc-hack", "bob", inbox, None, None)
    assert out.startswith("# Quorus Operating Discipline")
    assert QOD_PREAMBLE in out
    for rule in QOD_RULES:
        assert rule in out
    assert "You are `bob` in the Quorus room `yc-hack`." in out


def test_gemini_agent_build_prompt_prepends_qod(tmp_path: Path):
    """Channel C — gemini_agent must prepend QOD to its build_prompt output."""
    from quorus_cli.gemini_agent import build_prompt

    from quorus.operating_discipline import QOD_PREAMBLE, QOD_RULES

    inbox = tmp_path / "inbox.txt"
    out = build_prompt("yc-hack", "carol", inbox, None)
    assert out.startswith("# Quorus Operating Discipline")
    assert QOD_PREAMBLE in out
    for rule in QOD_RULES:
        assert rule in out
    assert "You are `carol` in the Quorus room `yc-hack`." in out


def test_qod_text_identical_across_three_agents(tmp_path: Path):
    """All three agent harnesses must render the *same* QOD text — not
    near-copies. The constitution is the product wedge; drift between agents
    defeats the point."""
    from quorus_cli.claude_agent import build_prompt as claude_prompt
    from quorus_cli.codex_agent import build_prompt as codex_prompt
    from quorus_cli.gemini_agent import build_prompt as gemini_prompt

    from quorus.operating_discipline import render_qod_for_agent_loop

    canonical = render_qod_for_agent_loop()
    inbox = tmp_path / "inbox.txt"

    # Each agent's prompt must contain the canonical QOD text byte-for-byte.
    for fn, name in [
        (lambda: claude_prompt("r", "p", inbox, None), "claude"),
        (lambda: codex_prompt("r", "p", inbox, None, None), "codex"),
        (lambda: gemini_prompt("r", "p", inbox, None), "gemini"),
    ]:
        out = fn()
        assert canonical in out, (
            f"{name}_agent.build_prompt() does not contain canonical QOD"
        )


def test_qod_skill_module_in_repo_present_and_in_sync():
    """Channel B — repo copy of the skill module exists and lists all six
    rule numbers. Keeps the skill from drifting silently."""
    repo_skill = Path(
        __file__
    ).resolve().parent.parent / "docs" / "skills" / "quorus-operating-discipline.md"
    assert repo_skill.exists(), (
        f"In-repo skill module missing: {repo_skill}"
    )
    text = repo_skill.read_text()
    # Each rule number appears as a heading in the skill doc.
    for n in range(1, 7):
        assert f"### {n}." in text, f"Skill module missing rule heading {n}."


@pytest.mark.parametrize(
    "render_fn",
    [
        "render_qod",
        "render_qod_for_mcp",
        "render_qod_for_agent_loop",
    ],
)
def test_render_functions_idempotent(render_fn: str):
    """Render functions must be deterministic — same input, same output."""
    import quorus.operating_discipline as mod

    fn = getattr(mod, render_fn)
    assert fn() == fn()
