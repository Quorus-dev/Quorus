"""Pydantic request models for relay route handlers."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from murmur.routes.helpers import _validate_name

VALID_MESSAGE_TYPES = {"chat", "claim", "status", "request", "alert", "sync", "brief", "subtask"}
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


class HeartbeatRequest(BaseModel):
    instance_name: str
    status: str = "active"
    room: str = ""

    @field_validator("instance_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("status")
    @classmethod
    def check_status(cls, v: str) -> str:
        if v not in {"active", "idle", "busy"}:
            raise ValueError("status must be one of: active, idle, busy")
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
