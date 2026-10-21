"""Owner-selected checkpoint command. Capsules never supply executable argv."""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from quorus.bounded import bounded


@dataclass(frozen=True)
class CheckConfig:
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_s: float = 45.0
    max_output_bytes: int = 65536

    def __post_init__(self):
        if (not isinstance(self.argv, tuple) or not 1 <= len(self.argv) <= 128
                or any(not isinstance(x, str) or not x or "\0" in x for x in self.argv)
                or sum(len(x.encode()) for x in self.argv) > 4096):
            raise ValueError("check argv must contain 1–128 nonempty tokens, at most 4096 bytes")
        if (not isinstance(self.cwd, str) or "\0" in self.cwd or len(self.cwd) > 512
                or Path(self.cwd).is_absolute() or ".." in Path(self.cwd).parts):
            raise ValueError("check cwd must be relative and inside the workspace")
        # quorus.bounded: a huge int (JSON allows 10**400) is out of range, never an
        # OverflowError (Codex on 2921ac4e).
        timeout = bounded(self.timeout_s, 0, 300)
        if type(self.timeout_s) not in (int, float) or timeout is None or timeout <= 0:
            raise ValueError("check timeout must be finite, positive and at most 300 seconds")
        if type(self.max_output_bytes) is not int or not 1024 <= self.max_output_bytes <= 1048576:
            raise ValueError("check output cap must be 1–1024 KiB")

    def record(self) -> dict:
        return {**asdict(self), "argv": list(self.argv), "timeout_s": float(self.timeout_s)}

    @property
    def config_id(self) -> str:
        raw = json.dumps(self.record(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def from_record(cls, value: dict) -> CheckConfig:
        fields = {"argv", "cwd", "timeout_s", "max_output_bytes"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("invalid check configuration")
        if not isinstance(value["argv"], list):
            raise ValueError("check argv must be a list")
        return cls(**{**value, "argv": tuple(value["argv"])})


def configure_binding(entry, previous, args, workspace: Path):
    """Rebinding a different repo revokes its check; no implicit check defaults.

    ``--handoff on|off`` is the room's live-handoff switch (dogfood plan §1). It
    can only be on beside a check, and goes off with the check: removing the
    check or moving the room to another repo turns it off too."""
    result = dict(entry) if isinstance(entry, dict) else {"path": entry, "mode": "default"}
    old_path = previous.get("path") if isinstance(previous, dict) else previous
    moved = bool(old_path) and Path(old_path).expanduser().resolve() != workspace
    if moved:
        result.pop("check", None)
    command = getattr(args, "check", None)
    timeout = getattr(args, "check_timeout", None)
    cap = getattr(args, "check_output_kb", None)
    if command is None:
        if timeout is not None or cap is not None:
            raise ValueError("--check-timeout and --check-output-kb require --check")
    elif command == "":
        result.pop("check", None)
    else:
        config = CheckConfig(tuple(shlex.split(command)),
                             timeout_s=45 if timeout is None else timeout,
                             max_output_bytes=65536 if cap is None else cap * 1024)
        result["check"] = config.record()
    handoff = getattr(args, "handoff", None)
    if handoff == "on":
        if not result.get("check"):
            raise ValueError("--handoff on needs a check: add --check \"<command>\"")
        result["handoff"] = True
    elif handoff == "off" or not result.get("check") or moved:
        # Another repo is another decision: a new check there doesn't carry the
        # old room's ON over (Codex on 2921ac4e). Only an explicit --handoff on.
        result.pop("handoff", None)
    return result if result.get("check") or isinstance(entry, dict) else entry
