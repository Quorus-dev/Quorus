"""Pydantic request models for relay route handlers."""

from __future__ import annotations

import re
import uuid as _uuid

from pydantic import BaseModel, Field, field_validator

from quorus.routes.helpers import _validate_name

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_uuid_ref(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not _UUID_RE.match(value):
        raise ValueError("must be a UUID")
    return str(_uuid.UUID(value))

VALID_MESSAGE_TYPES = {
    "chat", "claim", "status", "request", "alert", "sync", "brief", "subtask", "decision",
    "social", "bundle",
}
VALID_ROLES = {"builder", "reviewer", "researcher", "pm", "qa", "member"}


class SendMessageRequest(BaseModel):
    from_name: str
    to: str
    content: str

    @field_validator("from_name", "to")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RegisterWebhookRequest(BaseModel):
    instance_name: str
    callback_url: str
    secret: str = ""  # Per-webhook HMAC secret (optional, falls back to global)

    @field_validator("instance_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class CreateRoomRequest(BaseModel):
    name: str
    created_by: str
    private: bool = False

    @field_validator("name", "created_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RoomMessageRequest(BaseModel):
    from_name: str
    content: str
    message_type: str = "chat"
    reply_to: str | None = None
    brief_id: str | None = None
    # Stream B threading — optional root-id that groups this message with
    # all other messages sharing the same root. ``reply_to`` is the
    # parent-pointer (one level), ``thread_root_id`` is the conversation
    # anchor (any depth). Reflexd propagates root_id on replies so
    # nested conversations stay grouped in the TUI.
    thread_root_id: str | None = None

    @field_validator("from_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("message_type")
    @classmethod
    def check_message_type(cls, v: str) -> str:
        if v not in VALID_MESSAGE_TYPES:
            allowed = ", ".join(sorted(VALID_MESSAGE_TYPES))
            raise ValueError(f"message_type must be one of: {allowed}")
        return v

    @field_validator("reply_to", "thread_root_id")
    @classmethod
    def check_uuid_refs(cls, v: str | None) -> str | None:
        # ``reply_to`` and ``thread_root_id`` are rendered into HTML
        # attribute values in the dashboard. Forcing UUID format here
        # makes them uninjectable at the API boundary — see
        # tests/test_xss_reply_to.py for the threat model.
        # ``brief_id`` is intentionally NOT validated as a UUID: it is an
        # opaque CLI-generated reference that is not rendered in any
        # user-facing surface.
        return _validate_uuid_ref(v)


class JoinLeaveRequest(BaseModel):
    participant: str
    role: str = "member"

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            allowed = ", ".join(sorted(VALID_ROLES))
            raise ValueError(f"role must be one of: {allowed}")
        return v


class KickRequest(BaseModel):
    participant: str
    requested_by: str

    @field_validator("participant", "requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RenameRoomRequest(BaseModel):
    new_name: str
    requested_by: str

    @field_validator("new_name", "requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class DestroyRoomRequest(BaseModel):
    requested_by: str

    @field_validator("requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RoomWebhookRequest(BaseModel):
    callback_url: str
    registered_by: str
    secret: str = ""  # Per-webhook HMAC secret (optional, falls back to global)

    @field_validator("registered_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


WAITING_ON = {"", "pacing", "quota", "stuck"}


class HeartbeatRequest(BaseModel):
    instance_name: str
    status: str = "active"
    room: str = ""
    reason: str = Field(default="", max_length=500)
    # What a busy agent is doing right now ("editing relay.py"), shown on its
    # working line. Display text only: control characters stripped, capped.
    activity: str = ""
    # What a not-working agent is waiting on, for the "needs you" board:
    # "pacing" (its plan slot), "quota" (vendor limit) or "stuck" (its last
    # turn failed), until epoch seconds (0 = unknown). Not free text.
    waiting_on: str = ""
    waiting_until: float = Field(default=0.0, ge=0.0, le=4102444800.0)

    @field_validator("instance_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("status")
    @classmethod
    def check_status(cls, v: str) -> str:
        if v not in {"active", "idle", "busy", "degraded"}:
            raise ValueError("status must be one of: active, idle, busy, degraded")
        return v

    @field_validator("activity")
    @classmethod
    def check_activity(cls, v: str) -> str:
        return "".join(ch for ch in v if ch.isprintable())[:80].strip()

    @field_validator("waiting_on")
    @classmethod
    def check_waiting_on(cls, v: str) -> str:
        if v not in WAITING_ON:
            raise ValueError("waiting_on must be one of: " + ", ".join(sorted(WAITING_ON)))
        return v


class InviteJoinRequest(BaseModel):
    participant: str
    token: str

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class AckRequest(BaseModel):
    """Client-side message acknowledgment.

    Use ``ack_token`` to ACK all messages from a fetch at once,
    or ``delivery_ids`` to ACK specific messages (using the
    ``_delivery_id`` field returned in each message).
    """

    ack_token: str | None = None
    delivery_ids: list[str] | None = None
