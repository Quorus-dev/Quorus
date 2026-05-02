"""Regression tests for human-vs-agent identity disambiguation.

Issue arav reported: when typing physically in the TUI, his messages
showed as `@arav-codex` (the active profile's agent instance_name)
instead of `@arav` — the human owner was indistinguishable from his
own agents in chat. Fix: detect agent-flavored profiles by keyword,
prompt once for chat_identity, persist, and use a green-glyph marker
in the renderer for humans.
"""

from __future__ import annotations

from quorus_tui.chat import _is_human_sender

from quorus.tui_hub import _name_has_agent_keyword, _strip_agent_suffix


class TestAgentKeywordDetection:
    """`_name_has_agent_keyword` distinguishes humans from agents."""

    def test_plain_name_is_human(self):
        assert not _name_has_agent_keyword("arav")
        assert not _name_has_agent_keyword("aarya")

    def test_codex_suffix_is_agent(self):
        assert _name_has_agent_keyword("arav-codex")

    def test_claude_suffix_is_agent(self):
        assert _name_has_agent_keyword("arav-claude")
        assert _name_has_agent_keyword("arav-claude-1m")
        assert _name_has_agent_keyword("arav-claude-2")
        assert _name_has_agent_keyword("arav-claude-desktop")

    def test_gemini_suffix_is_agent(self):
        assert _name_has_agent_keyword("arav-gemini")

    def test_cursor_suffix_is_agent(self):
        assert _name_has_agent_keyword("arav-cursor")

    def test_chained_agent_suffix(self):
        assert _name_has_agent_keyword("arav-codex-claude-1m")
        assert _name_has_agent_keyword("arav-codex-codex")
        assert _name_has_agent_keyword("arav-codex-gemini")

    def test_human_label_suffixes_stay_human(self):
        """`-pm`, `-test`, `-auto` are role labels, not agents."""
        assert not _name_has_agent_keyword("arav-pm")
        assert not _name_has_agent_keyword("arav-test")
        assert not _name_has_agent_keyword("arav-auto")
        assert not _name_has_agent_keyword("aarya-test")

    def test_empty_name_is_not_agent(self):
        assert not _name_has_agent_keyword("")


class TestStripAgentSuffix:
    """`_strip_agent_suffix` derives a human name from an agent name."""

    def test_simple_strip(self):
        assert _strip_agent_suffix("arav-codex") == "arav"
        assert _strip_agent_suffix("arav-claude") == "arav"
        assert _strip_agent_suffix("arav-gemini") == "arav"

    def test_chained_strip(self):
        assert _strip_agent_suffix("arav-codex-claude-1m") == "arav"
        assert _strip_agent_suffix("arav-codex-codex") == "arav"

    def test_versioned_agent(self):
        assert _strip_agent_suffix("arav-claude-1m") == "arav"
        assert _strip_agent_suffix("arav-claude-desktop") == "arav"

    def test_plain_name_unchanged(self):
        assert _strip_agent_suffix("arav") == "arav"

    def test_human_role_label_kept(self):
        assert _strip_agent_suffix("arav-pm") == "arav-pm"


class TestIsHumanSender:
    """The chat renderer's view of whether a sender deserves the green
    glyph. Mirrors `_name_has_agent_keyword` semantics — humans get
    `●` in `success` style, agents in their hashed sender color."""

    def test_humans(self):
        assert _is_human_sender("arav")
        assert _is_human_sender("aarya-test")
        assert _is_human_sender("arav-pm")
        assert _is_human_sender("")

    def test_agents(self):
        assert not _is_human_sender("arav-codex")
        assert not _is_human_sender("arav-claude")
        assert not _is_human_sender("arav-claude-1m")
        assert not _is_human_sender("arav-gemini")
        assert not _is_human_sender("arav-codex-claude-1m")
