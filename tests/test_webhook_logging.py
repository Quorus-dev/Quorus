"""Tests that webhook delivery failures log structured, secret-free fields.

The retry + DLQ paths must log ``target``, ``webhook_host`` (hostname
only — no path or query), ``status_code``, ``attempt``, ``error_type``,
and must never include a full URL with query string or any request body.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from quorus.backends.memory import InMemoryWebhookBackend
from quorus.services import webhook_svc
from quorus.services.webhook_svc import WebhookJob, WebhookService


def _source() -> str:
    return inspect.getsource(webhook_svc)


def test_failure_logs_include_target_not_tenant_id():
    source = _source()
    assert "target=" in source, (
        "webhook failure logs must include target field"
    )
    assert "tenant_id=" not in source, (
        "the misleading tenant_id= log field must be renamed to target="
    )


def test_failure_logs_include_host_not_full_url():
    source = _source()
    assert "webhook_host=" in source, (
        "webhook failure logs must use webhook_host (host only), not the full URL"
    )
    # The hostname is extracted via urlparse().hostname — make sure we're
    # not logging the raw callback URL with path/query in the failure path.
    # Grep-level: find every warning/error logger call with 'Webhook' in it
    # and assert none of them pass `callback_url=` or `job.callback_url`
    # as a field.
    for bad in ("callback_url=", "job.callback_url,"):
        # Allow one mention for the legacy INFO success log, but the failure
        # warning sites must use webhook_host.
        for line in source.splitlines():
            stripped = line.strip()
            if "logger.warning" in stripped and bad in stripped:
                raise AssertionError(
                    f"webhook failure log leaks full URL via {bad!r}: {line}"
                )


def test_unparseable_url_falls_back_to_placeholder_not_raw_url():
    """When urlparse can't extract a hostname, the log field must fall back
    to the literal ``"<unparseable>"`` — never the raw URL (which can carry
    tokens in its query string)."""
    source = _source()
    assert '"<unparseable>"' in source
    for bad in ("or job.callback_url", "or callback_url"):
        for line in source.splitlines():
            if bad in line and "hostname" in line:
                raise AssertionError(
                    f"host fallback still uses the raw URL: {line.strip()}"
                )


def test_ssrf_block_logs_host_only():
    """The SSRF-block error logs must pass a hostname, never the full URL."""
    source = _source()
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if "SSRF blocked" in stripped:
            in_block = True
            continue
        if in_block:
            # The argument line immediately after the format string.
            assert ".hostname" in stripped, (
                f"SSRF-block log must pass urlparse().hostname, got: {stripped}"
            )
            in_block = False


def test_failure_logs_include_status_code():
    assert "status_code=" in _source()


def test_failure_logs_include_attempt():
    assert "attempt=" in _source()


def test_failure_logs_include_error_type():
    assert "error_type=" in _source()


def test_failure_logs_never_include_request_body():
    source = _source()
    # No logger.* call that dumps a full job body / payload / data field.
    for bad in ("body=", "payload=", "request_body=", "job.payload"):
        for line in source.splitlines():
            stripped = line.strip()
            if any(
                stripped.startswith(prefix)
                for prefix in ("logger.warning", "logger.error", "logger.info")
            ) and bad in stripped:
                raise AssertionError(
                    f"webhook log leaks payload/body via {bad!r}: {line}"
                )


def test_webhook_host_is_extracted_via_urlparse():
    """urlparse().hostname strips the path and query — using it (vs manual
    string slicing) is the correct way to avoid leaking tokens in query
    strings."""
    source = _source()
    assert "urlparse(" in source
    # And the host variable we log is the .hostname attribute.
    assert ".hostname" in source


# ---------------------------------------------------------------------------
# Real delivery-failure test (monkeypatched logger, not grep)
# ---------------------------------------------------------------------------


class _CaptureLogger:
    """Records structlog-style calls: (event, args, kwargs)."""

    def __init__(self):
        self.warnings: list[tuple] = []
        self.errors: list[tuple] = []
        self.infos: list[tuple] = []

    def warning(self, event, *args, **kwargs):
        self.warnings.append((event, args, kwargs))

    def error(self, event, *args, **kwargs):
        self.errors.append((event, args, kwargs))

    def info(self, event, *args, **kwargs):
        self.infos.append((event, args, kwargs))


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"server said {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


@pytest.mark.asyncio
async def test_delivery_failure_warning_has_structured_secret_free_fields(
    monkeypatch,
):
    """Drive a real failed delivery through ``_process_job`` and assert the
    WARNING carries ``target``/``webhook_host``/``status_code``/``attempt``/
    ``error_type`` — host only, no query-string token, no ``tenant_id``."""
    capture = _CaptureLogger()
    monkeypatch.setattr(webhook_svc, "logger", capture)

    svc = WebhookService(InMemoryWebhookBackend())

    async def _always_valid(url):
        return True

    class _FailingClient:
        async def post(self, url, json=None, headers=None):
            raise _FakeHTTPError(500)

    monkeypatch.setattr(svc, "validate_url_at_delivery", _always_valid)
    monkeypatch.setattr(svc, "_get_client", lambda: _FailingClient())

    # Start at MAX-1 so the failure lands in the DLQ (permanent) path and
    # no retry timers are scheduled into the test loop.
    job = WebhookJob(
        target="tenant-a",
        callback_url="https://hooks.example.com/cb/path?token=SUPERSECRETTOKEN",
        payload={"hello": "world"},
        attempt=webhook_svc._MAX_RETRIES - 1,
    )
    await svc._process_job(job)

    assert len(capture.warnings) == 1
    event, args, fields = capture.warnings[0]
    assert event == "Webhook delivery permanently failed"
    assert fields["target"] == "tenant-a"
    assert "tenant_id" not in fields
    assert fields["webhook_host"] == "hooks.example.com"
    assert fields["status_code"] == 500
    assert fields["attempt"] == webhook_svc._MAX_RETRIES
    assert fields["error_type"] == "_FakeHTTPError"
    # Host only — no scheme, path, or query anywhere in the logged fields.
    logged = repr((event, args, fields))
    assert "SUPERSECRETTOKEN" not in logged
    assert "/cb/path" not in logged
    assert "?" not in fields["webhook_host"]
    assert "/" not in fields["webhook_host"]
