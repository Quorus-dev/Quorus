"""Slice 0: human receipts, redelivery and reflexd queue ownership (no servers)."""
from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from quorus_cli import agent_cmd, cli, hooks
from quorus_cli import inbox_delivery as delivery

from quorus.backends.memory import InMemoryMessageBackend


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a: None)
    monkeypatch.setattr(hooks, "_resolve_auth", lambda: ("http://relay.test", "human", {}))
    monkeypatch.setattr(cli, "RELAY_URL", "http://relay.test")
    monkeypatch.setattr(cli, "INSTANCE_NAME", "human")
    monkeypatch.setattr(cli, "_auth_headers", lambda: {})
    monkeypatch.setattr(hooks.sys, "stdin", io.StringIO("{}"))
    backend = InMemoryMessageBackend()
    state = SimpleNamespace(backend=backend, now=1000, acks=[], gets=[], ack_fails=False)
    monkeypatch.setattr("quorus.backends.memory.time.time", lambda: state.now)

    def enqueue(n=1):
        for i in range(n):
            asyncio.run(backend.enqueue("t", "human", {
                "id": f"delivery-{i}", "message_id": "same-room-message",
                "room": "room", "from_name": "sender", "content": f"body-{i}",
            }))
    state.enqueue = enqueue

    def get(url, **kw):
        state.gets.append(url)
        if url.endswith("/peek"):
            data = {"count": asyncio.run(backend.peek("t", "human")),
                    "pending": asyncio.run(backend.pending_count("t", "human"))}
        else:
            messages, token = asyncio.run(backend.fetch("t", "human"))
            data = {"messages": messages, "ack_token": token}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    def post(url, **kw):
        if state.ack_fails:
            raise httpx.ConnectError("synthetic ACK outage")
        ids = kw["json"]["delivery_ids"]
        state.acks.extend(ids)
        asyncio.run(backend.ack_ids("t", "human", ids))
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    return state


@pytest.mark.parametrize("handler", [hooks.handle_cursor_session, hooks.handle_cursor_stop,
                                      hooks.handle_gemini_beforeagent, hooks.handle_claude_stop])
def test_human_hook_shows_once_across_prompts_and_acks(inbox, handler, capsys, tmp_path):
    inbox.enqueue(2)
    (tmp_path / ".quorus").mkdir(exist_ok=True)
    (tmp_path / ".quorus" / "pending-human.flag").touch()
    handler()
    assert "body-0" in capsys.readouterr().out
    assert inbox.acks == ["delivery-0", "delivery-1"]
    inbox.now += 61
    handler()
    assert "body-0" not in capsys.readouterr().out
    assert asyncio.run(inbox.backend.peek("t", "human")) == 0


def test_failed_ack_redelivers_after_60_seconds_without_showing_again(inbox, capsys):
    inbox.enqueue(2)
    inbox.ack_fails = True
    hooks.handle_gemini_beforeagent()
    assert "body-0" in capsys.readouterr().out
    inbox.now += 61
    inbox.ack_fails = False
    hooks.handle_gemini_beforeagent()
    assert "body-0" not in capsys.readouterr().out
    assert inbox.acks == ["delivery-0", "delivery-1"]  # retry previously shown receipts
    inbox.now += 61
    assert asyncio.run(inbox.backend.peek("t", "human")) == 0


def test_cap_neither_acks_nor_marks_unshown_messages(inbox, capsys):
    inbox.enqueue(23)
    hooks.handle_gemini_beforeagent()
    text = capsys.readouterr().out
    assert "body-3" in text and "body-0" not in text
    assert inbox.acks == [f"delivery-{i}" for i in range(3, 23)]
    inbox.now += 61
    hooks.handle_gemini_beforeagent()
    text = capsys.readouterr().out
    assert "body-0" in text and "body-2" in text and "body-3" not in text
    assert inbox.acks == [f"delivery-{i}" for i in [*range(3, 23), *range(3)]]


def test_failed_output_never_records_or_acks(inbox, monkeypatch):
    inbox.enqueue()
    batch = delivery.Batch("http://relay.test", "human", {},
                           hooks._fetch_unread("http://relay.test", {}, "human"))
    assert len(batch.shown) == 1
    # Handlers call commit only after print/flush. Exercise that boundary via CLI.
    inbox.now += 61
    def broken(*args, **kwargs):
        raise BrokenPipeError("synthetic closed output")
    monkeypatch.setattr("builtins.print", broken)
    with pytest.raises(BrokenPipeError):
        cli._cmd_inbox(SimpleNamespace(json=True, quiet=True))
    assert inbox.acks == []
    assert delivery._load_cursors(batch.key) == {}


@pytest.mark.parametrize("guard", ["marker", "live_daemon", "registered_agent"])
def test_agent_hook_and_cli_never_lease_or_ack_reflexds_queue(
    inbox, monkeypatch, tmp_path, guard, capsys,
):
    inbox.enqueue()
    root = tmp_path / "config"
    root.mkdir(exist_ok=True)
    if guard == "marker":
        (root / ".quorus-agent").touch()
    elif guard == "registered_agent":
        (root / "agents").mkdir()
        (root / "agents" / "human.json").touch()
    else:
        monkeypatch.setattr(agent_cmd, "_running_daemon_for", lambda *a: "pid 123")
    hooks.handle_gemini_beforeagent()
    cli._cmd_inbox(SimpleNamespace(json=True, quiet=True))
    assert "body-0" not in capsys.readouterr().out
    assert inbox.gets == [] and inbox.acks == []
    # reflexd's unchanged fetch/ACK protocol still sees it immediately, not after 60 s.
    messages, token = asyncio.run(inbox.backend.fetch("t", "human"))
    assert [m["id"] for m in messages] == ["delivery-0"]
    asyncio.run(inbox.backend.ack("t", "human", token))
    assert asyncio.run(inbox.backend.peek("t", "human")) == 0


def test_cli_receipts_are_shared_with_hooks(inbox, capsys):
    inbox.enqueue(2)
    inbox.ack_fails = True
    cli._cmd_inbox(SimpleNamespace(json=True, quiet=True))
    assert len(json.loads(capsys.readouterr().out)) == 2
    inbox.now += 61
    inbox.ack_fails = False
    hooks.handle_gemini_beforeagent()
    assert "body-0" not in capsys.readouterr().out
    assert inbox.acks == ["delivery-0", "delivery-1"]


def test_receipts_use_ids_not_uuid_order_or_canonical_room_id():
    state = {}
    messages = [{"_delivery_id": ident, "message_id": "same", "room": "r"}
                for ident in ("ffffffff", "00000000", "88888888")]
    assert len(delivery._filter_unseen(messages, state)) == 3
    delivery._mark_seen(state, messages)
    assert delivery._filter_unseen(messages, state) == []


def test_same_name_on_another_relay_has_separate_receipts(inbox):
    messages = [{"_delivery_id": "same-id", "room": "r"}]
    first = delivery.Batch("http://one", "human", {}, messages)
    first.commit()
    assert delivery.Batch("http://one", "human", {}, messages).shown == []
    assert delivery.Batch("http://two", "human", {}, messages).shown == messages


def test_cli_acks_only_printed_delivery_ids_not_the_whole_token(inbox, capsys):
    inbox.enqueue(3)
    cli._cmd_inbox(SimpleNamespace(json=True, quiet=True))
    shown = json.loads(capsys.readouterr().out)
    assert inbox.acks == [m["_delivery_id"] for m in shown]
    assert inbox.acks == [f"delivery-{i}" for i in range(3)]


def test_chunked_message_keeps_compound_delivery_id_in_ack(inbox, monkeypatch):
    compound = json.dumps(["stream-1", "stream-2"])
    message = {"_delivery_id": compound, "message_id": "logical-message", "room": "r"}
    posts = []
    def post(url, **kwargs):
        posts.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    batch = delivery.Batch("http://relay.test", "human", {}, [message])
    assert batch.shown == [message]
    batch.commit()
    assert posts == [{"delivery_ids": [compound]}]  # service expands the chunk IDs


def test_cursor_stop_keeps_pending_flag_when_batch_is_capped(inbox, capsys, tmp_path):
    inbox.enqueue(21)
    folder = tmp_path / ".quorus"
    folder.mkdir(exist_ok=True)
    sentinel = folder / "pending-human.flag"
    sentinel.touch()
    hooks.handle_cursor_stop()
    assert "body-0" not in capsys.readouterr().out and sentinel.exists()
    inbox.now += 61
    hooks.handle_cursor_stop()
    assert "body-0" in capsys.readouterr().out and not sentinel.exists()


def test_ownership_lookup_failure_never_leases_queue(inbox, monkeypatch):
    inbox.enqueue()
    def unavailable(*args):
        raise OSError("synthetic daemon discovery failure")
    monkeypatch.setattr(agent_cmd, "_running_daemon_for", unavailable)
    assert hooks._fetch_unread("http://relay.test", {}, "human") == []
    assert inbox.gets == [] and inbox.acks == []
