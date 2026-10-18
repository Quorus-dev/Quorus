"""Claude can report a vendor error as a JSON result with process exit 0."""

import json

import pytest

from quorus.runtime.failover import VendorFailure
from tests import test_reflexd_failover as flow
from tests.test_reflexd_adapters import _FakeProc

reflexd = flow.reflexd
setup = flow.setup


def result(text, *, is_error=True, **extra):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                       "result": text, "session_id": "sid-123", **extra})


@pytest.mark.parametrize("text, reason", [
    ("Usage limit reached", "quota"),
    ("Account quota exceeded", "quota"),
    ("Rate limit reached", "rate_limit"),
    ("Not logged in", "login"),
])
def test_error_result_is_a_typed_vendor_diagnosis(text, reason):
    reply, sid = reflexd._parse_claude_json(result(text))
    assert isinstance(reply, VendorFailure)
    assert reply.reason == reason
    assert sid == "sid-123"


def test_successful_answer_about_limits_is_not_a_failure():
    text = "The usage limit and quota checks passed"
    reply, sid = reflexd._parse_claude_json(result(text, is_error=False))
    assert reply == text and not isinstance(reply, VendorFailure)
    assert sid == "sid-123"


def test_unknown_error_stays_generic():
    reply, sid = reflexd._parse_claude_json(result("private unrecognised failure"))
    assert reply == "[reflexd] harness errored"
    assert not isinstance(reply, VendorFailure)
    assert sid == "sid-123"


def test_errors_array_is_diagnosed_without_result_text():
    reply, _ = reflexd._parse_claude_json(result(None, subtype="error_during_execution",
                                               errors=["usage limit reached"]))
    assert isinstance(reply, VendorFailure) and reply.reason == "quota"


@pytest.mark.parametrize("is_error", [True, False])
def test_exit_zero_result_drives_failover_only_for_errors(setup, monkeypatch, is_error):
    daemon, relay = setup(), flow.Relay()
    async def spawn(*args, **kwargs):
        return _FakeProc(stdout=result("Usage limit reached", is_error=is_error).encode(),
                         stderr=b"", returncode=0)
    monkeypatch.setattr(reflexd.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(reflexd.shutil, "which", lambda name: "/bin/harness")
    daemon.adapter = reflexd.HeadlessAdapter()
    flow.run(daemon, relay)
    if is_error:
        assert len(relay.handoffs) == 1
        assert relay.handoffs[0]["failover"]["reason"] == "quota"
        assert daemon._presence[0] == "degraded"
    else:
        assert not relay.handoffs and not relay.releases
        assert daemon._presence[0] == "active"
