"""The suite must pass when run by a woken agent with a live identity in env."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_ambient_quorus_identity_does_not_leak_into_tests():
    env = dict(os.environ)
    env.update({
        "QUORUS_RELAY_URL": "http://127.0.0.1:8787",
        "QUORUS_INSTANCE_NAME": "arav-claude",
        "QUORUS_RELAY_SECRET": "live-secret",
        "QUORUS_CONFIG_DIR": "/nonexistent/quorus-config",
        "REFLEXD_CODEX_SANDBOX": "workspace-write",
    })
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_config.py::test_load_config_missing_file_uses_defaults",
         "tests/test_reflexd_adapters.py::test_codex_adapter_invokes_correct_binary"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-2000:]
