"""The Claude Code plugin (plugins/quorus) stays in sync with the MCP server."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "quorus"
MIN_RUNTIME = "quorus-ai>=0.5.1"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketplace_lists_the_plugin_directory():
    market = _json(ROOT / ".claude-plugin" / "marketplace.json")
    [entry] = market["plugins"]
    assert entry["name"] == _json(PLUGIN / ".claude-plugin" / "plugin.json")["name"]
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_plugin_root_has_no_stray_mcp_config():
    # A plugin's root .mcp.json loads alongside plugin.json; the repo root's dev
    # .mcp.json must never ship inside the plugin.
    assert not (PLUGIN / ".mcp.json").exists()


def test_server_runs_from_pypi_with_env_names_the_server_reads():
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    server = manifest["mcpServers"]["quorus"]
    assert server["command"] == "uvx"
    # Pinned to the first release with relay-bound credentials and read-only
    # startup: an older quorus-mcp from PyPI would still leak (codex review).
    assert server["args"] == ["--from", MIN_RUNTIME, "quorus-mcp"]
    runtime = (ROOT / "packages" / "mcp" / "quorus_mcp" / "runtime.py").read_text()
    options = manifest["userConfig"]
    for env_name, ref in server["env"].items():
        assert f'"{env_name}"' in runtime, f"quorus-mcp never reads {env_name}"
        key = re.fullmatch(r"\$\{user_config\.(\w+)\}", ref).group(1)
        assert key in options
    assert options["relay_secret"]["sensitive"] and options["api_key"]["sensitive"]
    assert not options["relay_url"].get("sensitive")


@pytest.mark.parametrize("skill_path", [
    PLUGIN / "skills" / "quorus" / "SKILL.md",
    ROOT / "integrations" / "agent-skills" / "quorus" / "SKILL.md",
])
def test_skill_names_only_tools_the_server_has(skill_path):
    skill = skill_path.read_text(encoding="utf-8")
    assert skill.startswith("---\nname: quorus\ndescription: ")
    mcp_src = "\n".join(p.read_text() for p in (ROOT / "packages" / "mcp" / "quorus_mcp")
                        .glob("*.py"))
    defined = set(re.findall(r"^async def (\w+)\(", mcp_src, re.M))
    named = set(re.findall(r"`(\w+)\(", skill)) | set(re.findall(r"`(\w+)` ", skill))
    tools = {n for n in named if "_" in n and n.islower()}
    missing = sorted(t for t in tools if t not in defined)
    assert not missing, f"skill names tools quorus-mcp doesn't define: {missing}"


def test_agent_skill_for_clawhub_and_hermes():
    skill_dir = ROOT / "integrations" / "agent-skills" / "quorus"
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    assert f"name: {skill_dir.name}\n" in head  # Agent Skills: name matches its folder
    assert "description: " in head and "version: " in head
    # Every env var the text tells an agent to set is declared for ClawHub's scan.
    for env in set(re.findall(r"`(QUORUS_[A-Z_]+)`", body)):
        assert f"name: {env}" in head, f"{env} used but not declared in envVars"
    assert f'"args":["--from","{MIN_RUNTIME}","quorus-mcp"]' in body
    assert f'args: ["--from", "{MIN_RUNTIME}", "quorus-mcp"]' in body


@pytest.mark.parametrize("skill_path", [
    PLUGIN / "skills" / "quorus" / "SKILL.md",
    ROOT / "integrations" / "agent-skills" / "quorus" / "SKILL.md",
])
def test_skills_never_put_a_secret_in_argv_or_self_install(skill_path):
    # Codex, e60f44ee: no `--secret`/`--api-key` examples, and setup is the user's call.
    skill = " ".join(skill_path.read_text(encoding="utf-8").split())
    assert not re.search(r"--(secret|api-key)\b", skill)
    assert "never put a secret in a command line" in skill.lower()
    assert re.search(r"don't (run setup|do any of it) .*(on your own|yourself)", skill,
                     re.I | re.S)
