"""MCP registration inputs and results shared by client writers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WriteStatus = Literal["wrote", "updated", "not_installed", "error", "kept", "manual"]


@dataclass
class WriteResult:
    """Outcome of registering the Quorus MCP server with one client."""

    platform: str
    status: WriteStatus
    path: Path | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("wrote", "updated")


@dataclass
class McpEnv:
    """Everything a writer needs to produce a working MCP entry.

    ``instance_name_base`` is the human's name (e.g. ``arav``). Each
    writer derives its own agent identity by suffixing the platform
    key (``arav-claude``, ``arav-codex``) so the relay sees each
    client as a separate participant.

    If ``per_platform_keys`` is provided, each platform gets its own
    API key (fetched from ``/v1/auth/register-agent``). Otherwise
    all platforms share the same key (legacy behavior, causes identity
    mismatch errors on the relay).
    """

    command: str
    args: list[str]
    relay_url: str
    api_key: str = ""
    relay_secret: str = ""
    instance_name_base: str = ""
    per_platform_keys: dict[str, str] | None = None  # platform_key -> api_key
    explicit_name: bool = False

    def agent_identity(self, platform_key: str) -> str:
        base = self.instance_name_base.strip()
        if self.explicit_name:
            return base
        if not base:
            return platform_key
        if base.endswith(f"-{platform_key}"):
            return base
        return f"{base}-{platform_key}"

    def env_block(self, platform_key: str) -> dict[str, str]:
        env: dict[str, str] = {
            "QUORUS_RELAY_URL": self.relay_url,
            "QUORUS_INSTANCE_NAME": self.agent_identity(platform_key),
        }
        # Use per-platform key if available, else fall back to shared key
        platform_api_key = (
            self.per_platform_keys.get(platform_key)
            if self.per_platform_keys
            else None
        )
        if platform_api_key:
            env["QUORUS_API_KEY"] = platform_api_key
        elif self.api_key:
            env["QUORUS_API_KEY"] = self.api_key
        elif self.relay_secret:
            env["QUORUS_RELAY_SECRET"] = self.relay_secret
        return env


