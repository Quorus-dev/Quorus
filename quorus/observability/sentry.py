"""Sentry initialization with PHI scrubbing for the Quorus relay.

This module is a no-op when ``SENTRY_DSN`` is unset (so dev/test stays clean)
and when ``sentry-sdk`` is not installed (so cold-install paths don't break).

Auto-instrumentation via the FastAPI/Starlette integrations is deliberate —
do NOT call ``sentry_sdk.capture_exception`` from application code. The
integrations already capture unhandled exceptions in request handlers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub PHI/PII before sending to Sentry. Returns None to drop entirely.

    Drops:
      - request bodies (contain message content)
      - cookies (auth tokens)
      - Authorization / Cookie headers (JWTs, session cookies)
      - breadcrumbs that include participant identifiers (``from_name``, ``@user``)
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for h in ("authorization", "Authorization", "cookie", "Cookie"):
                headers.pop(h, None)

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for crumb in breadcrumbs.get("values", []) or []:
            msg = crumb.get("message", "") if isinstance(crumb, dict) else ""
            if "from_name" in msg or "@" in msg:
                crumb["message"] = "[scrubbed-identity]"

    return event


def init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is set. Returns True if active.

    Safe to call multiple times — sentry_sdk.init handles re-init internally.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("SENTRY_DSN not set, skipping Sentry init")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning(
            "sentry-sdk not installed; install with: pip install 'sentry-sdk[fastapi]>=1.45.0'"
        )
        return False

    release = (
        os.environ.get("FLY_RELEASE_VERSION")
        or os.environ.get("GIT_SHA")
        or "dev"
    )
    environment = os.environ.get("FLY_APP_NAME", "local").replace("quorus-", "")

    try:
        traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    except ValueError:
        logger.warning("Invalid SENTRY_TRACES_SAMPLE_RATE; falling back to 0.05")
        traces_sample_rate = 0.05

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        # Profiling stays off until we move off the free plan.
        profiles_sample_rate=0.0,
        # Block auto-PII collection at the SDK level (defense in depth on top of before_send).
        send_default_pii=False,
        before_send=_scrub_event,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
        ],
        # 4xx HTTPException is part of normal API contract; CancelledError is asyncio noise.
        ignore_errors=["HTTPException", "asyncio.CancelledError"],
    )
    logger.info("sentry init complete env=%s release=%s", environment, release)
    return True
