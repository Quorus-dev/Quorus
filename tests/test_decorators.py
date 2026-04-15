"""Tests for quorus.decorators — decorator-based agent API."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from quorus.decorators import Agent


@pytest.fixture
def agent():
    """Create an Agent with mocked HTTP client."""
    with patch("quorus.decorators.QuorusClient") as MockClient:
        mock_client = MagicMock()
        mock_client.receive.return_value = []
        mock_client.send.return_value = {"id": "m1"}
        mock_client.join.return_value = {"status": "joined"}
        MockClient.return_value = mock_client
        a = Agent("http://test:8080", "secret", "test-agent", poll_interval=0.05)
        yield a


def test_on_message_registers_handler(agent):
    @agent.on_message("dev")
    def handler(msg):
        pass

    assert len(agent._handlers) == 1
    assert agent._handlers[0][0] == "dev"
    assert agent._handlers[0][1] is None
    assert "dev" in agent._joined_rooms


def test_on_message_with_type_filter(agent):
    @agent.on_message("dev", type="claim")
    def handler(msg):
        pass

    assert agent._handlers[0][1] == "claim"


def test_dispatch_calls_matching_handler(agent):
    called = []

    @agent.on_message("dev")
    def handler(msg):
        called.append(msg)

    agent._dispatch({
        "room": "dev",
        "from_name": "alice",
        "content": "hello",
        "message_type": "chat",
    })

    assert len(called) == 1
    assert called[0]["content"] == "hello"


def test_dispatch_skips_own_messages(agent):
    called = []

    @agent.on_message("dev")
    def handler(msg):
        called.append(msg)

    agent._dispatch({
        "room": "dev",
        "from_name": "test-agent",
        "content": "hello",
        "message_type": "chat",
    })

    assert len(called) == 0


def test_dispatch_filters_by_type(agent):
    claims = []
    all_msgs = []

    @agent.on_message("dev", type="claim")
    def claim_handler(msg):
        claims.append(msg)

    @agent.on_message("dev")
    def all_handler(msg):
        all_msgs.append(msg)

    agent._dispatch({
        "room": "dev",
        "from_name": "alice",
        "content": "CLAIM: auth",
        "message_type": "claim",
    })
    agent._dispatch({
        "room": "dev",
        "from_name": "bob",
        "content": "hello",
        "message_type": "chat",
    })

    assert len(claims) == 1
    assert len(all_msgs) == 2


def test_dispatch_filters_by_room(agent):
    dev_msgs = []

    @agent.on_message("dev")
    def handler(msg):
        dev_msgs.append(msg)

    agent._dispatch({
        "room": "other",
        "from_name": "alice",
        "content": "hello",
        "message_type": "chat",
    })

    assert len(dev_msgs) == 0


def test_claim_decorator(agent):
    @agent.claim("dev", "build auth")
    def do_work():
        return "done"

    result = do_work()
    assert result == "done"
    assert agent.client.send.call_count == 2
    # First call: CLAIM
    args1 = agent.client.send.call_args_list[0]
    assert args1[0] == ("dev", "CLAIM: build auth", "claim")
    # Second call: STATUS
    args2 = agent.client.send.call_args_list[1]
    assert args2[0] == ("dev", "STATUS: build auth complete", "status")


def test_claim_decorator_on_failure(agent):
    @agent.claim("dev", "broken task")
    def do_work():
        raise ValueError("oops")

    with pytest.raises(ValueError):
        do_work()

    assert agent.client.send.call_count == 2
    args2 = agent.client.send.call_args_list[1]
    assert "ALERT" in args2[0][1]
    assert "oops" in args2[0][1]


def test_heartbeat_registers(agent):
    @agent.heartbeat("dev", interval=10)
    def status():
        return "idle"

    assert len(agent._heartbeats) == 1
    assert agent._heartbeats[0][0] == "dev"
    assert agent._heartbeats[0][1] == 10


def test_join_rooms(agent):
    @agent.on_message("dev")
    def handler(msg):
        pass

    @agent.on_message("ops")
    def ops_handler(msg):
        pass

    agent._join_rooms()
    assert agent.client.join.call_count == 2


def test_send(agent):
    agent.send("dev", "hello", "chat")
    agent.client.send.assert_called_once_with("dev", "hello", "chat")


def test_run_and_stop(agent):
    """Agent.run blocks; agent.stop terminates it."""
    @agent.on_message("dev")
    def handler(msg):
        pass

    # Make receive return messages once, then empty
    agent.client.receive.side_effect = [
        [{"room": "dev", "from_name": "alice", "content": "hi", "message_type": "chat"}],
        [],
        [],
    ]

    def stop_after_delay():
        time.sleep(0.15)
        agent.stop()

    stopper = threading.Thread(target=stop_after_delay, daemon=True)
    stopper.start()

    agent.run()  # blocks until stop

    assert not agent._running
    agent.client.join.assert_called()


def test_dispatch_handler_exception_doesnt_crash(agent):
    """A handler that raises shouldn't kill dispatch."""
    called = []

    @agent.on_message("dev")
    def bad_handler(msg):
        raise RuntimeError("boom")

    @agent.on_message("dev")
    def good_handler(msg):
        called.append(msg)

    # bad_handler is registered first, but good_handler should still run
    agent._dispatch({
        "room": "dev",
        "from_name": "alice",
        "content": "test",
        "message_type": "chat",
    })

    assert len(called) == 1
