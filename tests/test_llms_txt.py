"""llms.txt: the agent guide ships in the repo, the wheel and over MCP (A4)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parents[1]
_LLMS = _ROOT / "llms.txt"


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUORUS_RELAY_SECRET", "test-secret")
    monkeypatch.setenv("QUORUS_RELAY_URL", "http://localhost:8080")


def test_llms_txt_tells_an_agent_how_to_install_and_connect() -> None:
    text = _LLMS.read_text(encoding="utf-8")
    assert text.startswith("# Quorus\n\n> ")
    for needle in (
        "pipx install quorus-ai",
        "quorus-mcp",
        "QUORUS_RELAY_URL",
        "QUORUS_RELAY_SECRET",
        "QUORUS_API_KEY",
        "QUORUS_INSTANCE_NAME",
        "quorus init <your-name> --relay-url http://localhost:8080 --secret <secret>",
        "join_room(",
        "quorus rooms --json",
        "quorus://llms.txt",
    ):
        assert needle in text, needle


async def test_llms_txt_names_every_mcp_tool() -> None:
    """A tool added to the server without a line here fails the build."""
    from quorus_mcp.server import mcp

    text = _LLMS.read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z_]+)\(", text))
    tools = {t.name for t in await mcp.list_tools()}
    assert tools, "the MCP server registers no tools?"
    assert tools - documented == set(), f"undocumented tools: {sorted(tools - documented)}"


def test_wheel_ships_llms_txt_inside_the_quorus_package() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["llms.txt"] == "quorus/llms.txt"


async def test_mcp_serves_llms_txt_as_a_resource() -> None:
    from quorus_mcp.server import mcp

    resources = {str(r.uri) for r in await mcp.list_resources()}
    assert "quorus://llms.txt" in resources
    contents = list(await mcp.read_resource("quorus://llms.txt"))
    assert len(contents) == 1
    assert contents[0].content == _LLMS.read_text(encoding="utf-8")
    assert contents[0].mime_type == "text/plain"


def test_resource_prefers_the_installed_copy(tmp_path, monkeypatch) -> None:
    from quorus_mcp import docs_resource

    import quorus

    fake_pkg = tmp_path / "quorus"
    fake_pkg.mkdir()
    (fake_pkg / "__init__.py").write_text("")
    (fake_pkg / "llms.txt").write_text("installed copy")
    monkeypatch.setattr(quorus, "__file__", str(fake_pkg / "__init__.py"))
    assert docs_resource.llms_txt_path() == fake_pkg / "llms.txt"
    assert docs_resource.llms_txt() == "installed copy"
