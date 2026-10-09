"""Claude Code channel push must match the published contract.

Regression (2026-10-08): the server emitted ``{"message", "channel"}`` params
and a ``{"channel": "quorus"}`` capability. Claude Code requires
``{"content", "meta"}`` with identifier-only meta keys and drops anything
else silently, so no Quorus message ever surfaced in an open session.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
from quorus_mcp import channel

ME = "arav-claude-desktop"


def _msg(**kw):
    base = {"from_name": "arav", "room": "build", "message_id": "m1",
            "content": f"@{ME} please check the build", "message_type": "chat"}
    base.update(kw)
    return base


def test_params_match_channel_contract() -> None:
    params = channel.channel_params(_msg())
    assert set(params) == {"content", "meta"}
    assert params["content"] == f"@{ME} please check the build"
    assert params["meta"] == {"sender": "arav", "room": "build", "message_id": "m1"}
    for key in params["meta"]:
        assert re.fullmatch(r"[A-Za-z0-9_]+", key), key  # else Claude Code drops it


@pytest.mark.parametrize("msg,expected", [
    (_msg(), True),                                          # mentioned
    (_msg(content="@arav-claude do x"), False),              # someone else
    (_msg(content=f"@{ME}-2 hi"), False),                    # prefix of a longer name
    (_msg(room="", content="direct note"), True),            # DM
    (_msg(from_name=ME), False),                             # own echo
    (_msg(message_type="wake_intent"), False),               # system event
    (_msg(content="general chatter"), False),                # room noise
])
def test_default_mode_pushes_only_what_is_addressed_to_me(msg, expected) -> None:
    assert channel.should_push(msg, ME, mode="mentions") is expected


def test_all_and_off_modes() -> None:
    chatter = _msg(content="general chatter")
    assert channel.should_push(chatter, ME, mode="all") is True
    assert channel.should_push(_msg(), ME, mode="off") is False


async def test_server_sends_contract_shape_and_filters() -> None:
    from quorus_mcp import server

    sent = []
    session = AsyncMock()
    session.send_message = AsyncMock(side_effect=lambda m: sent.append(m))
    with (
        patch.object(server, "PUSH_NOTIFICATION_METHOD", channel.CHANNEL_METHOD),
        patch.object(server, "INSTANCE_NAME", ME),
        patch.object(server, "_get_active_session", AsyncMock(return_value=session)),
    ):
        await server._notify_active_session([_msg(), _msg(content="noise", message_id="m2")])
    assert len(sent) == 1
    notif = sent[0].message.root
    assert notif.method == "notifications/claude/channel"
    assert notif.params["content"].startswith(f"@{ME}")
    assert notif.params["meta"]["room"] == "build"


def test_capability_value_is_empty_object() -> None:
    from quorus_mcp import server

    if not server.SSE_ENABLED:
        pytest.skip("channel capability only advertised with SSE enabled")
    opts = server.mcp._mcp_server.create_initialization_options()
    assert opts.capabilities.experimental["claude/channel"] == {}
