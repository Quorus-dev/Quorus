"""The agent's own key for `quorus connect` snippets on relays that use API keys.

A snippet must never carry the user's own (parent) key: that acts as the user,
not the agent. So `connect` reuses the child key saved for the agent, or mints
one with ``/v1/auth/register-agent`` and saves it, and the snippet reads it
from the environment (docs/plans/connect-secret-snippets.md, "relays that use
API keys"). Nothing here prints a key.
"""

from __future__ import annotations

from pathlib import Path

from quorus.config import DEFAULT_RELAY_URL, ConfigManager, same_relay


class AgentKeyError(Exception):
    """`connect` can't give this agent a key; ``hint`` says what to do."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def canonical_name(name: str, parent: str, platform: str) -> tuple[str, str]:
    """``(agent name, suffix)``. A request for the parent's own name becomes the
    ``<parent>-<platform>`` child, so it never gets the parent key, and asking
    again finds the same saved key instead of rotating it (Codex on 319ab1dd)."""
    if name == parent:
        return f"{parent}-{platform}", platform
    prefix = f"{parent}-"
    suffix = name[len(prefix):].strip() if name.startswith(prefix) else ""
    if not suffix:
        raise AgentKeyError(
            f"The agent name must be yours ({parent}) or a child like {prefix}<suffix>.",
            f"try: quorus connect {platform} <room> {prefix}{platform}")
    return name, suffix


def agent_key(*, relay_url: str, name: str, parent: str, parent_key: str,
              platform: str, register, credential_file: Path) -> tuple[str, str]:
    """``(agent name, agent key)`` once the key is saved in *credential_file*.
    The caller must never print the key.

    *register* is ``cli._register_agent_identity_full``. Only the relay and
    key the profile itself holds are used: with this shell pointing at another
    relay, a child key saved here would be bound to the wrong relay, so it
    stops before calling anything (Codex on 1bb20571).
    """
    profile = ConfigManager(path=credential_file).load()
    if parent_key != profile.get("api_key") or not same_relay(
            relay_url, profile.get("relay_url") or DEFAULT_RELAY_URL):
        raise AgentKeyError(
            "This shell's API key or relay isn't the one your profile saves.",
            f"save it first: quorus init {parent} --relay-url {relay_url} --api-key - "
            "(reads the key from stdin), then rerun connect")
    agent, suffix = canonical_name(name, parent, platform)
    keys = profile.get("agent_api_keys")
    keys = keys if isinstance(keys, dict) else {}
    if keys.get(agent):
        return agent, keys[agent]  # the relay revokes earlier child keys on every call
    minted = register(relay_url=relay_url, parent_api_key=parent_key, suffix=suffix)
    if not minted:
        raise AgentKeyError(f"The relay didn't create an identity for {agent}.",
                            "check the relay is reachable: quorus doctor")
    agent, key = minted
    profile["agent_api_keys"] = {**keys, agent: key}
    ConfigManager(path=credential_file).save(profile)
    return agent, key
