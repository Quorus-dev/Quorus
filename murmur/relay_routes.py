"""Backward-compat shim — routes have moved to murmur/routes/.

Re-exports symbols that tests and other modules depend on.
This module will be removed once all consumers are updated.
"""

from __future__ import annotations

import os

from murmur.routes import router  # noqa: F401
from murmur.routes.invites import (
    INVITE_SECRET as INVITE_SECRET,
)
from murmur.routes.invites import (
    INVITE_TTL as INVITE_TTL,
)
from murmur.routes.invites import (
    _make_invite_token as _make_invite_token,
)
from murmur.routes.invites import (
    _verify_invite_token as _verify_invite_token,
)

# Config constants re-exported for tests that patch them
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
MESSAGE_TTL_SECONDS = int(os.environ.get("MESSAGE_TTL_SECONDS", str(24 * 60 * 60)))
MAX_ROOM_HISTORY = int(os.environ.get("MAX_ROOM_HISTORY", "200"))
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "60"))
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))
_LEGACY_TENANT = "_legacy"


def reset_state():
    """Reset all service state — called by tests.

    Re-initializes backends and services from scratch so every test
    starts with a clean slate.
    """
    from murmur.relay import _init_services, app
    from murmur.routes.analytics import reset_analytics
    _init_services(app)
    reset_analytics()
