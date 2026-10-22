"""Assemble a redacted capsule. Runtime transfer remains gated on the next slice."""

from __future__ import annotations

import hashlib
import json

from quorus.observability.redaction import redact_secrets
from quorus.protocol.handoff_capsule import (
    CAPSULE_BYTES,
    CheckpointRecord,
    HandoffCapsule,
    LimitContext,
    RepoSnapshot,
    Spent,
)
from quorus.runtime.check_config import CheckConfig


def build_capsule(*, root: str, hop: int, actor: str, task: str, repo: dict,
                  checkpoint: dict, config: CheckConfig | None, limit: dict,
                  spent: dict, tried: list[str] | None = None,
                  known_secrets: tuple[str, ...] = ()) -> HandoffCapsule:
    """Keep identity/snapshot fields intact; trim only informational text to fit.

    No command, commit subject, or model reply becomes an instruction. config is
    the local owner's configuration, never a value read out of a room message.
    Snapshot/limit identity is validated, not replaced with plausible defaults.
    """
    check = CheckpointRecord.model_validate(checkpoint).model_dump()
    if check["tail_trimmed"]:
        raise ValueError("capsule writer requires an untrimmed checkpoint record")
    if check.get("status") in ("ran", "passing"):
        if config is None or check.get("config_id") != config.config_id:
            raise ValueError("checkpoint does not match local check configuration")
        if any(check.get(k) != v for k, v in config.record().items()):
            raise ValueError("checkpoint command metadata mismatch")
    check["tail"] = redact_secrets(check.get("tail", ""), known_secrets)
    if "tail_sha256" in check:
        check["tail_sha256"] = hashlib.sha256(check["tail"].encode()).hexdigest()
    value = {"v": 2, "id": f"{root}:{hop}", "root": root, "hop": hop, "by": actor,
             "task": redact_secrets(task, known_secrets)[:120],
             "repo": RepoSnapshot.model_validate(repo).model_dump(), "check": check,
             "limit": LimitContext.model_validate(limit).model_dump(),
             "spent": Spent.model_validate(spent).model_dump(),
             "tried": [redact_secrets(x, known_secrets)[:120] for x in (tried or [])[:3]]}
    # Informational paths can be omitted; immutable snapshot identity cannot.
    value["repo"]["dirty_files"] = [redact_secrets(x, known_secrets)[:200]
                                     for x in value["repo"].get("dirty_files", [])[:32]]
    def wire():
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    if redact_secrets(wire(), known_secrets) != wire():
        raise ValueError("credential in capsule identity or check metadata")
    while len(wire().encode()) > CAPSULE_BYTES:
        if value["repo"]["dirty_files"]:
            value["repo"]["dirty_files"].pop()
        elif value["tried"]:
            value["tried"].pop()
        elif check["tail"]:
            check["tail"] = "\n".join(check["tail"].splitlines()[1:])
            check["tail_trimmed"] = True
        else:
            raise ValueError("required capsule fields exceed 4096 bytes")
    return HandoffCapsule.model_validate(value)
