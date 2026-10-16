"""Relay telemetry must not export credentials used by relay routes."""

import json

import pytest

from quorus.observability.sentry import _scrub_event


def _event():
    return {"message": "audit", "request": {
        "url": "http://relay/stream/alice?token=PRIVATE-token",
        "query_string": "token=PRIVATE-token",
        "headers": {"bOoTsTrAp-SeCrEt": "PRIVATE-bootstrap", "aUtHoRiZaTiOn": "PRIVATE-jwt",
                    "user-agent": "audit"},
    }}


def test_telemetry_removes_relay_credentials():
    result = _scrub_event(_event(), {})
    assert "PRIVATE-" not in json.dumps(result)
    assert result["request"]["headers"]["user-agent"] == "audit"


def test_sentry_transport_receives_no_credentials():
    sdk = pytest.importorskip("sentry_sdk")
    captured = []
    from sentry_sdk.transport import Transport

    class OfflineTransport(Transport):
        def capture_envelope(self, envelope):
            captured.append(envelope.get_event())

    # Capture the final serialized event without using a socket.
    client = sdk.Client(dsn="https://public@example.invalid/1", default_integrations=False,
                        transport=OfflineTransport(),
                        before_send=_scrub_event, send_default_pii=False)
    try:
        client.capture_event(_event())
        assert len(captured) == 1
        assert "PRIVATE-" not in json.dumps(captured, default=str)
    finally:
        client.close()
