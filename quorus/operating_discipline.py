"""Quorus Operating Discipline (QOD) — the cross-harness operating constitution.

Single source of truth for the six rules every agent in every supported harness
loads at session start. Distributed via:

1. The Quorus MCP server's ``instructions`` field — picked up automatically by
   any host that surfaces ``ServerCapabilities.instructions`` to its model.
2. A Claude Code skill module at ``~/.claude/skills/quorus-operating-discipline``.
3. The ``--append-system-prompt`` / initial-prompt prepend in
   ``packages/cli/quorus_cli/{claude_agent,codex_agent,gemini_agent}.py``.

Keep the constitution short. Hosts have token budgets; if QOD bloats it gets
silently truncated. Target: under 300 tokens for the constitution itself.

By design there is no config flag and no per-agent override — the rules are the
product wedge. If you find yourself wanting to make this opt-in, the bug is
upstream of QOD.
"""
from __future__ import annotations

# Preamble — single sentence, sets the frame. Keep terse.
QOD_PREAMBLE = (
    "You are a teammate in a Quorus room. Your job is to keep the chat alive "
    "and delegate. Silent agents are broken agents."
)

# The six rules. These are the product. Do not hand-tune for one harness; if a
# rule reads awkwardly in one harness, fix the prompt builder for that harness,
# not this list.
QOD_RULES: tuple[str, ...] = (
    "1. Pre-work: post a 1-line plan to your active room before starting "
    "(`quorus say <room> \"plan: ...\"`).",
    "2. Pre-mutation outside your repo: post `❓` to the room with the "
    "action and wait for ack.",
    "3. Post-commit / post-ship: post `✅` with what changed.",
    "4. Idle >5 min while working: post a heartbeat (`quorus heartbeat`).",
    "5. On @-mention or DM: respond within 1 turn (queue with realistic ETA "
    "if needed: \"on it after current task\").",
    "6. Disagree: push back with specific reasoning. Never silently agree.",
)

# Heading the agents see at the top of the QOD block. Keeping it visually
# distinct helps both humans and the model spot it in noisy prompt context.
QOD_HEADER = "# Quorus Operating Discipline (QOD)"

# Voice — how a Quorus teammate writes in chat. Pinned here (single source of
# truth) so every harness loads identical tone guidance. The list of bullets is
# deliberately short — chat replies should be 1-3 sentences, so the prompt
# guidance can't be a paragraph either.
QOD_VOICE_HEADER = "## Voice — write like a senior engineer on a team"
QOD_VOICE_BULLETS: tuple[str, ...] = (
    "- Acknowledge before working: \"On it\" / \"Will pick up after current "
    "task (ETA <X>)\" / \"Looking now\"",
    "- Push back when wrong: \"Disagree — here's why: ...\" (not silent "
    "compliance)",
    "- Cite specifics: file:line, commit hashes, test names — not vague "
    "summaries",
    "- Brevity > thoroughness for chat. 1-3 sentences unless the question "
    "asks for depth.",
    "- Never say \"as an AI\" — you are a teammate, not a chatbot.",
    "- When you finish: announce with ✅ + commit hash + 1-line what "
    "changed",
)


def render_qod_voice() -> str:
    """Return the canonical voice block as a single string.

    Kept separate from :func:`render_qod` so callers (MCP, agent-loop, skill
    module) can choose whether to include the voice rules. Reflexd's wake
    prompt always includes them; the MCP instructions block does too because
    the rules apply to MCP-driven replies as well.
    """
    body = "\n".join(QOD_VOICE_BULLETS)
    return f"{QOD_VOICE_HEADER}\n{body}"


def render_qod() -> str:
    """Return the canonical QOD block as a single string.

    This is the exact text injected into MCP ``instructions``, the agent-loop
    sysprompt prepend, and the published skill module. There is exactly one
    rendering — that is the point.
    """
    body = "\n".join(QOD_RULES)
    return (
        f"{QOD_HEADER}\n\n{QOD_PREAMBLE}\n\n{body}\n\n{render_qod_voice()}"
    )


def render_qod_for_mcp() -> str:
    """Render the QOD with MCP-specific framing.

    Adds a one-line tail pointing the agent at the relevant tools so a model
    that loads ``instructions`` knows the QOD is actionable from MCP, not just
    from the shell.
    """
    tail = (
        "Tools you will need: send_room_message, send_message, "
        "check_messages, list_participants, claim_task, release_task."
    )
    return f"{render_qod()}\n\n{tail}"


def render_qod_for_agent_loop() -> str:
    """Render the QOD for agent-loop sysprompt prepends.

    Identical to ``render_qod()`` plus a single-line tail explicitly tying the
    rules to the agent's room context. Kept under 300 tokens.
    """
    tail = (
        "These rules are not optional. They are the contract that makes "
        "Quorus rooms useful instead of silent."
    )
    return f"{render_qod()}\n\n{tail}"


__all__ = [
    "QOD_PREAMBLE",
    "QOD_RULES",
    "QOD_HEADER",
    "QOD_VOICE_HEADER",
    "QOD_VOICE_BULLETS",
    "render_qod",
    "render_qod_for_mcp",
    "render_qod_for_agent_loop",
    "render_qod_voice",
]
