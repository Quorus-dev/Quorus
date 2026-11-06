"""Where `quorus join` saves the identity an invite gives it.

The first join on a machine becomes its setup. A join to the relay and name
already in use refreshes that profile. Anything else (another relay, or another
name) is saved as a profile of its own, and the current setup, plus every MCP
client registered from it, keeps pointing where it did. Before this, joining a
teammate's room on their relay rewrote config.json, so the next start moved
that over profiles/default.json and the user's own key was gone (2026-11-04).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from quorus.config import ConfigManager, same_relay
from quorus.profiles import ProfileManager


@dataclass(frozen=True)
class Saved:
    slug: str | None  # profile written; None when it went to config.json
    current: bool     # this identity is now the one quorus and MCP clients use
    previous: dict    # the setup that was current before, {} if none


def _slug_for(relay_url: str, name: str, pm: ProfileManager) -> str:
    host = (urlsplit(relay_url).hostname or "relay").lower()
    base = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")[:50] or "relay"
    slug, n = base, 1
    while True:
        existing = pm.get(slug)
        if existing is None or (same_relay(existing.get("relay_url", ""), relay_url)
                                and existing.get("instance_name") == name):
            return slug
        n += 1
        slug = f"{base}-{n}"


def save(data: dict, config_dir: Path | None = None) -> Saved:
    """Save *data* (relay_url, instance_name and a credential) without
    overwriting a different identity that's already set up."""
    pm = ProfileManager(config_dir)
    pm.migrate_legacy_if_needed()  # a flat config.json becomes profiles/default
    current = pm.current()
    previous = (pm.get(current) if current else None) or {}
    if not previous and not pm.list():
        ConfigManager(pm.pointer_path).save(data)
        return Saved(None, True, {})
    relay_url, name = data["relay_url"], data["instance_name"]
    if (current and same_relay(previous.get("relay_url", ""), relay_url)
            and previous.get("instance_name") == name):
        kept = {k: v for k, v in previous.items() if k not in ("api_key", "relay_secret")}
        pm.save(current, {**kept, **data})
        return Saved(current, True, previous)
    slug = _slug_for(relay_url, name, pm)
    pm.save(slug, data)
    if not previous:  # profiles exist but none is current: use the new one
        pm.set_current(slug)
        return Saved(slug, True, {})
    return Saved(slug, False, previous)
