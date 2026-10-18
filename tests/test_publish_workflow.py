"""publish.yml lists each release in the MCP Registry without any stored token."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WF = (_ROOT / ".github" / "workflows" / "publish.yml").read_text()


def _job(name: str) -> str:
    return _WF.split(f"\n  {name}:\n", 1)[1].split("\n  # ", 1)[0]


def test_registry_publish_runs_after_pypi_with_oidc_only():
    job = _job("mcp-registry")
    assert "needs: publish" in job  # the registry checks the PyPI package
    assert "id-token: write" in job and "contents: read" in job
    assert "./mcp-publisher login github-oidc" in job
    assert "./mcp-publisher publish" in job
    assert "secrets." not in job  # nothing to leak: OIDC is the credential


def test_publisher_binary_is_pinned_and_checksum_verified():
    job = _job("mcp-registry")
    assert "releases/latest" not in job
    assert re.search(r'MCP_PUBLISHER_VERSION: "\d+\.\d+\.\d+"', job)
    assert re.search(r'MCP_PUBLISHER_SHA256: "[0-9a-f]{64}"', job)
    assert "sha256sum -c -" in job
    # Checked before it's unpacked, and nothing is piped into a shell.
    assert job.index("sha256sum -c -") < job.index("tar xzf")
    assert not re.search(r"\|\s*(sh|bash)\b", job)


def test_registry_job_refuses_a_server_json_that_does_not_match_the_tag():
    job = _job("mcp-registry")
    assert 'GITHUB_REF_NAME"].removeprefix("v")' in job
    assert "versions == {tag}" in job
