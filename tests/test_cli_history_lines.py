"""0.6.3 audit (multi-person BUG 7): `quorus history` printed social verbs as raw JSON."""

import json

from quorus_cli import cli
from rich.console import Console


def _plain(markup: str) -> str:
    c = Console(width=200, record=True, color_system=None)
    c.print(markup)
    return c.export_text().strip()


def _msg(content: str, sender: str = "aarya-codex") -> dict:
    return {"from_name": sender, "content": content, "timestamp": "2026-10-20T04:30:47"}


def test_a_social_verb_reads_as_a_line_not_json() -> None:
    env = {"kind": "social", "verb": "disagree", "actor": "aarya-codex", "room_id": "r",
           "payload": {"ref_message_id": "m1", "reason": "deleting the tests hides the bug",
                       "mode": "blocking"}}
    line = _plain(cli._format_chat_msg(_msg(json.dumps(env))))
    assert "/disagree blocking: deleting the tests hides the bug" in line
    assert '"kind"' not in line


def test_verbs_with_a_target_name_it() -> None:
    env = {"kind": "social", "verb": "handoff", "actor": "a", "room_id": "r",
           "payload": {"to": "arav-claude", "ttl_seconds": 300}}
    assert "/handoff → @arav-claude" in _plain(cli._format_chat_msg(_msg(json.dumps(env))))


def test_plain_text_and_brackets_print_as_written() -> None:
    assert "see [/tmp] and [bold]x" in _plain(cli._format_chat_msg(_msg("see [/tmp] and [bold]x")))
    assert "{not json" in _plain(cli._format_chat_msg(_msg("{not json")))
