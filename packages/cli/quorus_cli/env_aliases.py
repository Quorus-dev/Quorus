"""Let the CLI read the QUORUS_* names that MCP entries and agent configs use.

The CLI reads RELAY_URL / RELAY_SECRET / API_KEY / INSTANCE_NAME; the MCP
server reads QUORUS_RELAY_URL and friends. With only the QUORUS_* names set,
the CLI used to fall back to localhost:8080 and say "start the relay"
(FRICTION 2026-11-02).

The QUORUS_* names count only as one relay's settings, anchored on
QUORUS_RELAY_URL. A lone QUORUS_API_KEY or QUORUS_RELAY_SECRET exported
shell-wide stays ignored (FRICTION #104). When RELAY_URL names a different
relay, none of them apply, so a credential never reaches a relay it wasn't
issued for. The copies exist only while load_config() reads them, so child
processes (reflexd, agents) never inherit them.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import TextIO

from quorus.config import same_relay

_ALIASES = (("QUORUS_RELAY_URL", "RELAY_URL"),
            ("QUORUS_RELAY_SECRET", "RELAY_SECRET"),
            ("QUORUS_API_KEY", "API_KEY"),
            ("QUORUS_INSTANCE_NAME", "INSTANCE_NAME"))


def _clean(env: MutableMapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def aliases(environ: MutableMapping[str, str] | None = None,
            stderr: TextIO | None = None) -> dict[str, str]:
    """The unprefixed values to fill in, without changing anything.

    Names only ever appear in warnings, never values.
    """
    env = os.environ if environ is None else environ
    err = sys.stderr if stderr is None else stderr
    anchor = _clean(env, "QUORUS_RELAY_URL")
    if not anchor:
        return {}
    current_url = _clean(env, "RELAY_URL")
    if current_url and not same_relay(current_url, anchor):
        err.write("quorus: RELAY_URL and QUORUS_RELAY_URL name different relays; "
                  "using RELAY_URL and ignoring every QUORUS_* setting\n")
        return {}
    fill: dict[str, str] = {}
    for src, dst in _ALIASES:
        value = _clean(env, src)
        if not value:
            continue
        current = _clean(env, dst)
        if current:
            if current != value and src != "QUORUS_RELAY_URL":
                err.write(f"quorus: {src} and {dst} are both set and differ; using {dst}\n")
            continue
        fill[dst] = value
    return fill


@contextmanager
def applied(environ: MutableMapping[str, str] | None = None,
            stderr: TextIO | None = None) -> Iterator[list[str]]:
    """Fill in the aliases for the duration of the block, then remove them."""
    env = os.environ if environ is None else environ
    fill = aliases(env, stderr)
    for name, value in fill.items():
        env[name] = value
    try:
        yield sorted(fill)
    finally:
        for name in fill:
            env.pop(name, None)
