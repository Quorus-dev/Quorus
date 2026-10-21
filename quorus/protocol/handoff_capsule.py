"""Never Stuck's bounded data format; not yet accepted by runtime /defer."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SHA = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=200)]
CAPSULE_BYTES = 4096


class Data(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "allow_inf_nan": False}


class RepoSnapshot(Data):
    main: Text
    base: SHA
    branch: Text
    head: SHA
    tree: SHA
    unfinished: Text
    parked_commit: SHA | None = None
    dirty_files: list[Text] = Field(default_factory=list, max_length=32)


class CheckpointRecord(Data):
    status: Literal["ran", "passing", "none", "skipped", "timed_out", "error", "modified_tree"]
    head: SHA
    tree: SHA | None = None
    argv: list[Annotated[str, Field(min_length=1, max_length=4096)]] | None = Field(
        default=None, max_length=128)
    cwd: str | None = Field(default=None, max_length=512)
    timeout_s: float | None = Field(default=None, gt=0, le=300)
    max_output_bytes: int | None = Field(default=None, ge=1024, le=1048576)
    config_id: Digest | None = None
    exit: int | None = Field(default=None, ge=0, le=255)
    truncated: bool = False
    ran_at: str | None = Field(default=None, max_length=64)
    wall_s: float | None = Field(default=None, ge=0)
    tail: str = Field(default="", max_length=1500)
    tail_sha256: Digest | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        if len(self.tail.encode()) > 1500:
            raise ValueError("checkpoint tail exceeds 1500 bytes")
        digest = hashlib.sha256(self.tail.encode()).hexdigest()
        if self.tail_sha256 is not None and self.tail_sha256 != digest:
            raise ValueError("checkpoint tail hash mismatch")
        if self.status in ("ran", "passing"):
            required = (self.tree, self.argv, self.cwd, self.timeout_s, self.max_output_bytes,
                        self.config_id, self.ran_at, self.tail_sha256)
            if any(v is None for v in required) or self.wall_s is None:
                raise ValueError("completed checkpoint requires complete evidence")
            when = datetime.fromisoformat(self.ran_at.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("checkpoint timestamp needs timezone")
            if self.exit is None or (self.status == "ran" and self.exit == 0):
                raise ValueError("only an actual nonzero failure is ran")
            if self.status == "passing" and self.exit != 0:
                raise ValueError("passing requires exit zero")
        return self


class LimitContext(Data):
    vendor: str = Field(min_length=1, max_length=32)
    reason: Literal["quota", "rate_limit", "login"]
    window: Literal["limit"] = "limit"
    kind: Literal["reported", "observed", "guess"]
    source: Literal["claude.statusline", "claude.stream", "codex.token_count",
                    "msg.retry_in", "msg.try_again_at", "msg.no_time"]
    seen_at: str = Field(max_length=64)
    resets_at: str | None = Field(default=None, max_length=64)
    used_pct: float | None = Field(default=None, ge=0, le=100)

    @field_validator("seen_at", "resets_at")
    @classmethod
    def utc_timestamp(cls, value):
        if value is None:
            return value
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if when.tzinfo is None or not 0 <= when.timestamp() <= 4102444800:
            raise ValueError("limit timestamp requires timezone and bounded epoch")
        return when.astimezone(timezone.utc).isoformat()

    def presence_reading(self) -> dict:
        """Presence uses epoch seconds; the capsule uses ISO for the same instants."""
        result = self.model_dump(exclude={"vendor", "reason"})
        for name in ("seen_at", "resets_at"):
            result[name] = (datetime.fromisoformat(result[name]).timestamp()
                            if result[name] else None)
        return result

    @classmethod
    def from_presence(cls, reading: dict, *, vendor: str, reason: str):
        value = dict(reading)
        for name in ("seen_at", "resets_at"):
            epoch = value.get(name)
            if epoch is None and name == "resets_at":
                continue
            if type(epoch) not in (int, float) or not 0 <= epoch <= 4102444800:
                raise ValueError("invalid presence epoch")
            value[name] = datetime.fromtimestamp(epoch, timezone.utc).isoformat()
        return cls.model_validate({**value, "vendor": vendor, "reason": reason})

    @model_validator(mode="after")
    def validate_source(self):
        expected = ("reported" if self.source in ("claude.statusline", "claude.stream",
                                                  "codex.token_count")
                    else "guess" if self.source == "msg.no_time" else "observed")
        if self.kind != expected:
            raise ValueError("limit kind/source mismatch")
        if self.resets_at is not None:
            elapsed = (datetime.fromisoformat(self.resets_at)
                       - datetime.fromisoformat(self.seen_at)).total_seconds()
            if not 0 < elapsed <= 691200:
                raise ValueError("invalid vendor reset time")
        if self.kind == "guess" and self.resets_at is not None:
            raise ValueError("guess cannot claim a vendor reset")
        if self.kind == "observed" and self.resets_at is None:
            raise ValueError("observed reset missing")
        if self.kind == "reported" and self.used_pct is None:
            raise ValueError("reported percentage missing")
        if self.kind != "reported" and self.used_pct is not None:
            raise ValueError("percentage requires reported source")
        return self


class Spent(Data):
    turns: int = Field(ge=0)
    wall_s: float = Field(ge=0)
    attempts: int = Field(ge=0)


class HandoffCapsule(Data):
    v: Literal[2] = 2
    id: str = Field(min_length=1, max_length=210)
    root: str = Field(min_length=1, max_length=200)
    hop: int = Field(ge=1, le=2)
    task: str = Field(min_length=1, max_length=120)
    by: str = Field(min_length=1, max_length=200)
    repo: RepoSnapshot
    check: CheckpointRecord
    tried: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=3)
    spent: Spent
    limit: LimitContext

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.id != f"{self.root}:{self.hop}":
            raise ValueError("capsule id must bind root and hop")
        if self.check.head != self.repo.head:
            raise ValueError("checkpoint head mismatch")
        if self.check.tree is not None and self.check.tree != self.repo.tree:
            raise ValueError("checkpoint tree mismatch")
        if len(self.model_dump_json().encode()) > CAPSULE_BYTES:
            raise ValueError("capsule exceeds 4096 bytes")
        return self
