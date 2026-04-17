"""Per-workspace profile storage.

Layout under ``~/.quorus/``:

  config.json           # pointer: {"current": "<slug>", "profiles": [...]}
  profiles/
    default.json        # legacy-migrated profile
    work.json
    personal.json

Each profile file holds the same shape the pre-multi-workspace ``config.json``
held: ``relay_url``, ``instance_name``, ``relay_secret``, ``api_key``, plus
optional ``workspace_label`` and ``workspace_id``.

Writes are atomic (``O_CREAT | 0o600`` tmp file, then ``os.replace``).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from quorus.config import CONFIG_FILENAME, resolve_config_dir

PROFILES_SUBDIR = "profiles"
POINTER_FILENAME = CONFIG_FILENAME  # ~/.quorus/config.json
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid profile slug {slug!r}. "
            "Use lowercase letters, numbers, hyphens, underscores (max 63 chars)."
        )


class ProfileManager:
    """Single source of truth for profile files + current-pointer."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir or resolve_config_dir()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def pointer_path(self) -> Path:
        return self._config_dir / POINTER_FILENAME

    @property
    def profiles_dir(self) -> Path:
        return self._config_dir / PROFILES_SUBDIR

    def _profile_path(self, slug: str) -> Path:
        _validate_slug(slug)
        return self.profiles_dir / f"{slug}.json"

    # ── read ─────────────────────────────────────────────────────────────

    def list(self) -> list[str]:
        if not self.profiles_dir.exists():
            return []
        slugs = [
            p.stem for p in self.profiles_dir.iterdir()
            if p.is_file() and p.suffix == ".json"
        ]
        return sorted(slugs)

    def current(self) -> str | None:
        data = self._read_pointer()
        slug = data.get("current")
        if not isinstance(slug, str) or not slug:
            return None
        return slug

    def get(self, slug: str) -> dict[str, Any] | None:
        path = self._profile_path(slug)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return None

    def current_profile(self) -> dict[str, Any] | None:
        slug = self.current()
        if slug is None:
            return None
        return self.get(slug)

    # ── write ────────────────────────────────────────────────────────────

    def save(self, slug: str, data: dict[str, Any]) -> None:
        _validate_slug(slug)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        path = self._profile_path(slug)
        _atomic_write_json(path, data)
        # Keep pointer's `profiles` list fresh.
        pointer = self._read_pointer()
        listed = pointer.get("profiles")
        if not isinstance(listed, list):
            listed = []
        if slug not in listed:
            listed.append(slug)
        pointer["profiles"] = sorted(listed)
        self._write_pointer(pointer)

    def set_current(self, slug: str) -> None:
        _validate_slug(slug)
        if not self._profile_path(slug).exists():
            raise FileNotFoundError(f"No such profile: {slug}")
        pointer = self._read_pointer()
        pointer["current"] = slug
        self._write_pointer(pointer)

    def delete(self, slug: str) -> None:
        _validate_slug(slug)
        path = self._profile_path(slug)
        if path.exists():
            path.unlink()
        pointer = self._read_pointer()
        listed = pointer.get("profiles")
        if isinstance(listed, list) and slug in listed:
            listed.remove(slug)
            pointer["profiles"] = listed
        if pointer.get("current") == slug:
            pointer["current"] = None
        self._write_pointer(pointer)

    # ── legacy migration ─────────────────────────────────────────────────

    def migrate_legacy_if_needed(self) -> str | None:
        """If config.json is the old flat shape, move it to profiles/default.json.

        Returns the migrated slug (``"default"``) or ``None``.
        """
        if not self.pointer_path.exists():
            return None
        try:
            raw = json.loads(self.pointer_path.read_text())
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        # Pointer shape has "current" or "profiles" and no "relay_url" / "api_key".
        if "relay_url" in raw or "api_key" in raw or "instance_name" in raw:
            slug = "default"
            self.save(slug, raw)
            self.set_current(slug)
            return slug
        return None

    # ── pointer I/O ──────────────────────────────────────────────────────

    def _read_pointer(self) -> dict[str, Any]:
        if not self.pointer_path.exists():
            return {}
        try:
            raw = json.loads(self.pointer_path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        # If this is still the legacy flat shape, refuse to treat it as a
        # pointer — caller should migrate explicitly.
        if "relay_url" in raw or "api_key" in raw:
            return {}
        return raw

    def _write_pointer(self, data: dict[str, Any]) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.pointer_path, data)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
