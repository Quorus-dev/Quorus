"""Console-script wrapper for ``quorus-relay``.

``quorus/relay.py`` validates its environment at IMPORT time — no
``RELAY_SECRET`` and no ``DATABASE_URL`` means the process exits before
anything runs. That fail-fast is correct for a server (a relay that boots
half-configured is worse than one that refuses), but it also meant
``quorus-relay --help`` printed a configuration error instead of help —
a rough first command for someone who just installed Quorus.

This wrapper answers ``--help`` / ``--version`` before importing the relay,
then hands off unchanged. The guard is untouched for real starts.
"""

from __future__ import annotations

import sys

USAGE = """quorus-relay — start the Quorus coordination relay

Usage:
  quorus-relay                 Start the relay (reads config from the environment)
  quorus-relay --help          Show this message
  quorus-relay --version       Show the installed version

Required (at least one):
  RELAY_SECRET                 Shared secret for legacy bearer auth
  DATABASE_URL                 Postgres URL (enables account-based auth)

Common options (environment variables):
  PORT                         Port to bind (default: 8080)
  JWT_SECRET                   Signing key for participant tokens
  REDIS_URL                    Enables shared rate limits, presence, and the
                               cross-replica speaker auction
  MESSAGES_FILE                File-mode message store (no Postgres needed)
  ALLOW_LEGACY_AUTH=1          Accept the legacy bearer secret
  LOG_LEVEL                    INFO (default), WARNING, DEBUG

Health check: GET /health   ·   API docs: GET /docs
"""


def main() -> None:
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return
    if any(a in ("-V", "--version") for a in argv):
        try:
            from importlib.metadata import version

            print(f"quorus-relay {version('quorus')}")
        except Exception:
            print("quorus-relay (version unknown)")
        return
    # Import is deliberately deferred: quorus.relay enforces its config at
    # import time, and that must not fire for --help/--version.
    from quorus.relay import main as _relay_main

    _relay_main()
