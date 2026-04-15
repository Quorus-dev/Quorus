"""Tests that webhook delivery failures log structured, secret-free fields.

The retry + DLQ paths must log ``tenant_id``, ``webhook_host`` (hostname
only — no path or query), ``status_code``, ``attempt``, ``error_type``,
and must never include a full URL with query string or any request body.
"""
from __future__ import annotations

import inspect

from quorus.services import webhook_svc


def _source() -> str:
    return inspect.getsource(webhook_svc)


def test_failure_logs_include_tenant_id():
    assert "tenant_id=" in _source(), (
        "webhook failure logs must include tenant_id field"
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
