"""`quorus` must not crash on a non-UTF-8 console (Windows cp1252 default).

Regression (2026-10-09): `quorus version` died with UnicodeEncodeError on the
banner's block glyphs on every fresh Windows install (CI cold-install).
"""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("argv", [["version"], ["--help"]])
def test_cli_survives_cp1252_console(argv: list[str], tmp_path) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "HOME": str(tmp_path),
           "QUORUS_CONFIG_DIR": str(tmp_path / ".q")}
    out = subprocess.run(
        [sys.executable, "-c",
         f"import sys; from quorus_cli.cli import main; sys.argv=['quorus', *{argv!r}]; main()"],
        env=env, capture_output=True, timeout=60,
    )
    assert b"UnicodeEncodeError" not in out.stderr, out.stderr[-400:]
    assert out.returncode == 0, out.stderr[-400:]
