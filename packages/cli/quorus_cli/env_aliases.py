"""Let the CLI read the QUORUS_* names that MCP entries and agent configs use.

The CLI reads RELAY_URL / RELAY_SECRET / INSTANCE_NAME; the MCP server reads
QUORUS_RELAY_URL and friends. With only the QUORUS_* names set, the CLI used to
fall back to localhost:8080 and say "start the relay" (FRICTION 2026-11-02).
Copying them across before load_config() keeps its relay-bound credential
rules unchanged: a URL for another relay still withholds the saved secret.
"""
from __future__ import annotations

import os
import sys
from typing import MutableMapping, TextIO

_ALIASES = (("QUORUS_RELAY_URL", "RELAY_URL"),
            ("QUORUS_RELAY_SECRET", "RELAY_SECRET"),
            ("QUORUS_INSTANCE_NAME", "INSTANCE_NAME"))


def apply(environ: MutableMapping[str, str] | None = None,
          stderr: TextIO | None = None) -> list[str]:
    """Copy set QUORUS_* values onto their CLI names; return the names set.

    An unprefixed name that is already set wins, with a one-line warning when
    the two differ. A shell-wide QUORUS_API_KEY on its own stays ignored, since
    it broke every tool on secret relays (FRICTION #104). It only applies
    alongside QUORUS_RELAY_URL, as part of one relay's settings.
    """
    env = os.environ if environ is None else environ
    err = sys.stderr if stderr is None else stderr
    pairs = list(_ALIASES)
    if (env.get("QUORUS_RELAY_URL") or "").strip():
        pairs.append(("QUORUS_API_KEY", "API_KEY"))
    applied: list[str] = []
    for src, dst in pairs:
        value = (env.get(src) or "").strip()
        if not value:
            continue
        current = (env.get(dst) or "").strip()
        if current:
            if current != value:
                err.write(f"quorus: {src} and {dst} are both set and differ; "
                          f"using {dst}\n")
            continue
        env[dst] = value
        applied.append(dst)
    return applied
