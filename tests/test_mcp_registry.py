"""The MCP Registry entry (server.json) matches the package it points at.

The registry verifies a PyPI server by the `mcp-name:` comment in the package
README, and clients start it with `uvx quorus-ai`, so both must stay true.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import quorus

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 (CI)
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parent.parent


def _server() -> dict:
    return json.loads((_ROOT / "server.json").read_text())


def test_server_json_matches_the_package() -> None:
    server, pyproject = _server(), tomllib.loads((_ROOT / "pyproject.toml").read_text())
    (pkg,) = server["packages"]
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == pyproject["project"]["name"]
    assert server["version"] == pkg["version"] == quorus.__version__
    assert pkg["transport"] == {"type": "stdio"}
    # Registry clients run `uvx <identifier>`: that command must be the MCP server.
    scripts = pyproject["project"]["scripts"]
    assert scripts[pkg["identifier"]] == scripts["quorus-mcp"]
    assert len(server["description"]) <= 100


def test_the_readme_carries_the_ownership_marker() -> None:
    first = (_ROOT / "README.md").read_text().splitlines()[0]
    assert re.fullmatch(r"<!-- mcp-name: (\S+) -->", first).group(1) == _server()["name"]


def test_every_listed_env_var_is_one_the_server_reads() -> None:
    runtime = (_ROOT / "packages" / "mcp" / "quorus_mcp" / "runtime.py").read_text()
    for var in _server()["packages"][0]["environmentVariables"]:
        assert f'"{var["name"]}"' in runtime, var["name"]
        assert var["description"]
    secrets = {v["name"] for v in _server()["packages"][0]["environmentVariables"]
               if v.get("isSecret")}
    assert secrets == {"QUORUS_RELAY_SECRET", "QUORUS_API_KEY"}
