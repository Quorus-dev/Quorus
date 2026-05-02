"""Cold-install regression test — the "demo won't open" gate.

Locks in the April 23 2026 hackathon failure mode: a cold `pipx install`
producing a broken binary. There are two flavors:

* The default flavor invokes ``scripts/cold_install_smoke.py`` against the
  ``quorus`` and ``quorus-relay`` binaries already on ``PATH``. This is the
  fast pre-merge check — it doesn't reinstall, it just exercises the
  shipped binaries end-to-end.

* The Docker flavor (``test_cold_install_in_docker``) is the gold standard:
  build a fresh image with no cached wheels, ``pipx install`` from the
  current checkout, run the smoke. Skipped cleanly when Docker isn't
  available so this file is safe to import in CI cells without it.

Run all of them with:
    pytest tests/test_cold_install.py -v

Skip the Docker variant explicitly with:
    pytest tests/test_cold_install.py -v -k "not docker"
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_PY = REPO_ROOT / "scripts" / "cold_install_smoke.py"
SMOKE_SH = REPO_ROOT / "scripts" / "cold_install_smoke.sh"


def _free_port() -> int:
    """Pick a free localhost port to avoid clobbering 18080 in dev."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _has_quorus_binaries() -> bool:
    return shutil.which("quorus") is not None and shutil.which("quorus-relay") is not None


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5, check=False
        )
        return out.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Fast path: smoke against PATH-installed binaries
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _has_quorus_binaries(),
    reason="quorus / quorus-relay not on PATH — install with `pipx install -e .`",
)
def test_cold_install_smoke_runs_clean() -> None:
    """The smoke script must complete in <60s with exit 0.

    This catches the YC-hackathon failure: the binary installs but the
    relay won't start, or the round-trip path is broken at the wire level.
    Pytest passes the relay/round-trip steps that import-time tests miss.
    """
    assert SMOKE_PY.exists(), f"smoke script missing at {SMOKE_PY}"
    port = _free_port()
    result = subprocess.run(
        [sys.executable, str(SMOKE_PY), "--port", str(port), "--timeout", "60"],
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "cold-install smoke FAILED\n"
            f"stdout:\n{result.stdout.decode(errors='replace')}\n"
            f"stderr:\n{result.stderr.decode(errors='replace')}"
        )


# ---------------------------------------------------------------------------
# Gold-standard path: full pipx cold install inside Docker
# ---------------------------------------------------------------------------

DOCKERFILE = """\
FROM python:{py}-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    bash curl ca-certificates git \\
 && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m pip install --no-cache-dir pipx \\
 && python -m pipx ensurepath
ENV PATH=/root/.local/bin:$PATH
COPY . /src
WORKDIR /src
RUN pipx install --force --pip-args="--no-cache-dir" /src
CMD ["bash", "scripts/cold_install_smoke.sh", "--skip-install"]
"""


@pytest.mark.integration
@pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available — skipping containerized cold-install smoke",
)
@pytest.mark.parametrize("py_version", ["3.11"])  # smoke just one row to keep CI cheap
def test_cold_install_in_docker(tmp_path: Path, py_version: str) -> None:
    """Full cold install inside a fresh container — the strongest possible gate.

    This is the test that would have caught April 23 2026: it runs as if a
    user had just done `pipx install` on their laptop, with no shared state
    from the test runner.
    """
    dockerfile = tmp_path / "Dockerfile.cold"
    dockerfile.write_text(DOCKERFILE.format(py=py_version))

    image_tag = f"quorus-cold-smoke:py{py_version.replace('.', '')}"
    build = subprocess.run(
        [
            "docker", "build",
            "--no-cache",  # true cold install
            "-f", str(dockerfile),
            "-t", image_tag,
            str(REPO_ROOT),
        ],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build failed\n"
            f"stdout:\n{build.stdout.decode(errors='replace')[-4000:]}\n"
            f"stderr:\n{build.stderr.decode(errors='replace')[-4000:]}"
        )

    run = subprocess.run(
        ["docker", "run", "--rm", image_tag],
        capture_output=True,
        timeout=180,
        check=False,
        env={**os.environ, "DOCKER_BUILDKIT": "1"},
    )
    if run.returncode != 0:
        pytest.fail(
            "containerized smoke failed\n"
            f"stdout:\n{run.stdout.decode(errors='replace')}\n"
            f"stderr:\n{run.stderr.decode(errors='replace')}"
        )
