"""The relay access logger must not persist query credentials."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entry", ["cli", "asgi"])
def test_relay_access_log_redacts_stream_tokens(entry):
    script = """
import copy
import logging.config
import sys
from unittest.mock import patch
import uvicorn
from quorus import relay

def run(*args, **kwargs):
    config = copy.deepcopy(kwargs.get('log_config', uvicorn.config.LOGGING_CONFIG))
    config['handlers']['access']['stream'] = sys.stdout
    logging.config.dictConfig(config)
    logging.getLogger('uvicorn.access').info(
        '%s - "%s %s HTTP/%s" %d', '127.0.0.1:123', 'GET',
        '/stream/alice?token=PRIVATE-stream-key&other=ok', '1.1', 200)
"""
    if entry == "cli":
        script += ("\nasync def serve(self, sockets=None):\n    run()\n"
                   "\nwith patch.object(uvicorn.Server, 'serve', serve):\n    relay.main()\n")
    else:
        script += "\nrun()\n"
    # Other tests leave DATABASE_URL set; production mode then demands Redis.
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "REDIS_URL")}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=15, env=env)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-stream-key" not in result.stdout
    assert "other=ok" in result.stdout
