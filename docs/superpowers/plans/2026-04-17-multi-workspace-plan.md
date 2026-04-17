# Multi-Workspace Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a single Quorus install hold multiple workspace identities, with a TUI-native live switcher, CLI commands, and MCP env-var selection.

**Architecture:** Profile files live under `~/.quorus/profiles/<slug>.json`. The top-level `~/.quorus/config.json` becomes a pointer `{"current": "<slug>", "profiles": [...]}`. A new `ProfileManager` is the single source of truth; `ConfigManager.load()` becomes a facade that returns the current profile's data for backward compatibility. The TUI refactors `run_hub` into an outer loop over an inner `_run_session` that can restart cleanly on `/workspace <slug>`.

**Tech Stack:** Python 3.10+, pytest, argparse, FastAPI (relay — untouched), Rich (TUI), readchar.

**Spec:** `docs/superpowers/specs/2026-04-17-multi-workspace-design.md`

**Build sequence:** Four independent commits — ProfileManager → ConfigManager facade → CLI → TUI. Commits 1–3 are independent; commit 4 depends on 1–3.

---

## File Structure

**Create:**
- `quorus/profiles.py` — ProfileManager class + legacy migration (new module, ~180 lines)
- `tests/test_profiles.py` — unit tests for ProfileManager (~250 lines)
- `tests/test_config_compat.py` — ensures `ConfigManager.load()` still works after facade wiring (~120 lines)
- `tests/test_cli_workspaces.py` — CLI subcommand tests (~200 lines)
- `tests/test_tui_workspace_switch.py` — TUI switch lifecycle test (~150 lines)

**Modify:**
- `quorus/config.py` — `ConfigManager` gets a `profile=` kwarg and `load()` delegates to ProfileManager (~40 lines added)
- `packages/cli/quorus_cli/cli.py` — new subcommands: `workspaces`, `login`, `whoami`; new `-w/--workspace` global flag (~200 lines added)
- `packages/mcp/quorus_mcp/server.py` — `QUORUS_PROFILE` env support in the load chain (~15 lines changed near line 23–37)
- `packages/tui/quorus_tui/hub.py` — refactor `run_hub` → `_run_session` + outer loop; add `/workspace` slash command; header shows workspace label (~120 lines added/changed)

---

## Task 1: ProfileManager module

**Files:**
- Create: `quorus/profiles.py`
- Test: `tests/test_profiles.py`

- [ ] **Step 1.1: Write the failing test for empty state**

Create `tests/test_profiles.py`:

```python
"""Unit tests for ProfileManager (multi-workspace storage)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Give each test a clean ~/.quorus/ equivalent."""
    return tmp_path


def test_list_is_empty_on_fresh_dir(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.list() == []
    assert pm.current() is None
    assert pm.current_profile() is None
```

- [ ] **Step 1.2: Run test, confirm it fails**

```bash
pytest tests/test_profiles.py::test_list_is_empty_on_fresh_dir -v
```
Expected: `ModuleNotFoundError: No module named 'quorus.profiles'`

- [ ] **Step 1.3: Create the minimal ProfileManager**

Create `quorus/profiles.py`:

```python
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
```

- [ ] **Step 1.4: Run first test, confirm it passes**

```bash
pytest tests/test_profiles.py::test_list_is_empty_on_fresh_dir -v
```
Expected: PASS.

- [ ] **Step 1.5: Add save+list+current+get tests**

Append to `tests/test_profiles.py`:

```python
def test_save_creates_profile_file(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://example.test", "api_key": "sk_x"})

    path = tmp_config_dir / "profiles" / "work.json"
    assert path.exists()
    # 0o600 perms (owner rw only)
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = json.loads(path.read_text())
    assert loaded["relay_url"] == "https://example.test"


def test_list_returns_sorted_slugs(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("zeta", {"relay_url": "x"})
    pm.save("alpha", {"relay_url": "x"})
    assert pm.list() == ["alpha", "zeta"]


def test_set_current_and_current_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1", "api_key": "k1"})
    pm.save("personal", {"relay_url": "u2", "api_key": "k2"})
    pm.set_current("personal")
    assert pm.current() == "personal"
    data = pm.current_profile()
    assert data is not None
    assert data["api_key"] == "k2"


def test_set_current_rejects_missing_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    with pytest.raises(FileNotFoundError):
        pm.set_current("ghost")


def test_get_returns_none_for_missing(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.get("nobody") is None


def test_get_returns_none_on_corrupt_file(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    (tmp_config_dir / "profiles").mkdir(parents=True)
    (tmp_config_dir / "profiles" / "broken.json").write_text("{ not json")
    assert pm.get("broken") is None


def test_delete_removes_file_and_unsets_current(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")
    pm.delete("work")
    assert pm.list() == []
    assert pm.current() is None
    assert not (tmp_config_dir / "profiles" / "work.json").exists()


def test_delete_missing_profile_is_noop(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.delete("never-existed")  # should not raise
```

- [ ] **Step 1.6: Run tests, confirm all pass**

```bash
pytest tests/test_profiles.py -v
```
Expected: all green.

- [ ] **Step 1.7: Add slug-validation tests**

Append to `tests/test_profiles.py`:

```python
def test_invalid_slug_rejected(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    for bad in ["UPPER", "has space", "dot.slug", "", "-leading", "with/slash"]:
        with pytest.raises(ValueError):
            pm.save(bad, {"relay_url": "x"})


def test_valid_slugs_accepted(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    for good in ["work", "acme-corp", "a", "with_under", "team2"]:
        pm.save(good, {"relay_url": "x"})
    assert set(pm.list()) == {"work", "acme-corp", "a", "with_under", "team2"}
```

- [ ] **Step 1.8: Run tests**

```bash
pytest tests/test_profiles.py -v
```
Expected: all green.

- [ ] **Step 1.9: Add legacy-migration tests**

Append to `tests/test_profiles.py`:

```python
def test_migrate_legacy_flat_config(tmp_config_dir: Path) -> None:
    # Old shape: relay_url/api_key at top level, no "current"/"profiles".
    legacy = {
        "relay_url": "https://legacy.test",
        "instance_name": "aarya",
        "api_key": "sk_old",
    }
    (tmp_config_dir / "config.json").write_text(json.dumps(legacy))

    pm = ProfileManager(config_dir=tmp_config_dir)
    slug = pm.migrate_legacy_if_needed()

    assert slug == "default"
    assert pm.current() == "default"
    data = pm.current_profile()
    assert data is not None
    assert data["relay_url"] == "https://legacy.test"
    assert data["api_key"] == "sk_old"
    # Pointer file is now clean (no relay_url at top level).
    pointer = json.loads((tmp_config_dir / "config.json").read_text())
    assert "relay_url" not in pointer
    assert pointer["current"] == "default"


def test_migrate_noop_on_pointer_shape(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")
    # Running migration again should do nothing.
    assert pm.migrate_legacy_if_needed() is None
    assert pm.current() == "work"


def test_migrate_noop_on_missing_config(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.migrate_legacy_if_needed() is None


def test_migrate_noop_on_corrupt_legacy(tmp_config_dir: Path) -> None:
    (tmp_config_dir / "config.json").write_text("{not valid json")
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.migrate_legacy_if_needed() is None
```

- [ ] **Step 1.10: Run all profile tests + lint**

```bash
pytest tests/test_profiles.py -v
ruff check quorus/profiles.py tests/test_profiles.py
```
Expected: all green, ruff clean.

- [ ] **Step 1.11: Commit 1**

```bash
git add quorus/profiles.py tests/test_profiles.py
git commit -m "$(cat <<'EOF'
feat(profiles): ProfileManager + legacy migration

New quorus.profiles module: per-workspace profile files under
~/.quorus/profiles/<slug>.json with a pointer in config.json.
Atomic 0o600 writes; legacy flat config auto-migrates to profile
'default' on migrate_legacy_if_needed().

No caller changes in this commit — ConfigManager still reads the
pointer file directly for now.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Task 2: ConfigManager facade wiring

**Files:**
- Modify: `quorus/config.py`
- Test: `tests/test_config_compat.py`

- [ ] **Step 2.1: Write failing compat test**

Create `tests/test_config_compat.py`:

```python
"""ConfigManager stays backward-compatible when profiles exist."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorus.config import ConfigManager
from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_load_returns_current_profile_when_profiles_exist(
    tmp_config_dir: Path,
) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test", "api_key": "sk_w"})
    pm.save("personal", {"relay_url": "https://personal.test", "api_key": "sk_p"})
    pm.set_current("work")

    data = ConfigManager().load()
    assert data["relay_url"] == "https://work.test"
    assert data["api_key"] == "sk_w"


def test_load_auto_migrates_legacy_flat_config(tmp_config_dir: Path) -> None:
    (tmp_config_dir / "config.json").write_text(
        json.dumps({"relay_url": "https://legacy.test", "api_key": "sk_old"})
    )

    data = ConfigManager().load()
    assert data["relay_url"] == "https://legacy.test"

    # After migration, there should be a profile file and a pointer.
    pm = ProfileManager(config_dir=tmp_config_dir)
    assert pm.current() == "default"
    assert (tmp_config_dir / "profiles" / "default.json").exists()


def test_load_returns_empty_dict_when_no_config(tmp_config_dir: Path) -> None:
    assert ConfigManager().load() == {}


def test_load_with_explicit_profile(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test"})
    pm.save("personal", {"relay_url": "https://personal.test"})
    pm.set_current("work")

    # Asking for a specific profile bypasses "current".
    data = ConfigManager(profile="personal").load()
    assert data["relay_url"] == "https://personal.test"


def test_load_with_missing_explicit_profile_returns_empty(
    tmp_config_dir: Path,
) -> None:
    assert ConfigManager(profile="ghost").load() == {}
```

- [ ] **Step 2.2: Run test, confirm failure**

```bash
pytest tests/test_config_compat.py -v
```
Expected: failures — `ConfigManager` doesn't accept `profile=` kwarg, doesn't migrate.

- [ ] **Step 2.3: Extend ConfigManager**

Modify `quorus/config.py`. Replace the `ConfigManager` class (lines 108–152) with this version — the `save()` method is unchanged; only `__init__` and `load()` change, plus a helper import at the top:

At the top of the file, after existing imports, add:

```python
# Imported lazily inside methods to avoid a circular import
# (profiles.py depends on resolve_config_dir from this module).
```

Replace the ConfigManager class body:

```python
class ConfigManager:
    """Load/save config with a single source of truth for the path.

    Behavior:

    - When ``profile`` is given, ``load()`` returns that profile's data
      (or ``{}`` if missing/corrupt).
    - When ``profile`` is ``None`` (default), ``load()`` returns the
      CURRENT profile's data, falling back to legacy auto-migration on
      first call, then falling back to direct pointer-file read if no
      profile layout has been set up yet (returns ``{}`` on fresh installs).
    - ``save(data)`` writes to ``self.path`` with 0o600 atomic rename.
      When ``profile`` is set, ``self.path`` resolves to the profile
      file; otherwise it resolves to the pointer path (backward-compat
      for callers writing the legacy flat shape — migration picks that
      up on the next load).
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        profile: str | None = None,
    ) -> None:
        self._explicit_path = path
        self._profile = profile

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        if self._profile is not None:
            from quorus.profiles import ProfileManager, PROFILES_SUBDIR
            return (
                resolve_config_dir() / PROFILES_SUBDIR / f"{self._profile}.json"
            )
        return resolve_config_file()

    def load(self) -> dict[str, Any]:
        # Explicit-path callers (tests, CLI --config pointing at a file)
        # get raw file contents, no profile resolution.
        if self._explicit_path is not None:
            return _read_json_or_empty(self._explicit_path)

        from quorus.profiles import ProfileManager

        pm = ProfileManager()

        # Explicit profile kwarg — read that profile or empty.
        if self._profile is not None:
            data = pm.get(self._profile)
            return data or {}

        # Default behavior: auto-migrate legacy, then return current.
        pm.migrate_legacy_if_needed()
        current = pm.current_profile()
        if current is not None:
            return current
        # No profiles and no legacy to migrate → empty.
        return {}

    def save(self, data: dict[str, Any]) -> None:
        path = self.path
        if _is_legacy_dir(path.parent) or _is_legacy_dir(path.parent.parent):
            raise ValueError(
                f"Refusing to write config to legacy path {path}. "
                "Move your config to ~/.quorus/."
            )
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


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}
```

- [ ] **Step 2.4: Run compat tests**

```bash
pytest tests/test_config_compat.py -v
```
Expected: all green.

- [ ] **Step 2.5: Run full existing config test suite — must stay green**

```bash
pytest tests/test_config.py tests/test_config_manager.py tests/test_config_resolution.py -v
```
Expected: all green. If any test depends on `load()` returning the raw pointer file, update that test to reflect the new contract OR use `ConfigManager(path=<explicit>)` to opt out of profile resolution.

- [ ] **Step 2.6: Lint**

```bash
ruff check quorus/config.py tests/test_config_compat.py
```
Expected: clean.

- [ ] **Step 2.7: Commit 2**

```bash
git add quorus/config.py tests/test_config_compat.py
git commit -m "$(cat <<'EOF'
feat(config): wire ConfigManager to ProfileManager facade

ConfigManager.load() now returns the current profile's data.
Legacy flat config.json auto-migrates to profiles/default.json
on first call. New `profile=` kwarg reads a specific profile.
Explicit path= callers bypass profile resolution (backward
compat for tests and tools pointing at a specific file).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Task 3: CLI — workspaces, login, whoami, -w flag

**Files:**
- Modify: `packages/cli/quorus_cli/cli.py`
- Test: `tests/test_cli_workspaces.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/test_cli_workspaces.py`:

```python
"""CLI workspace subcommands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _run_cli(argv: list[str]) -> int:
    """Invoke quorus_cli.cli.main() with a fake argv. Returns exit code."""
    from quorus_cli import cli as cli_mod
    with patch.object(cli_mod.sys, "argv", ["quorus"] + argv):
        try:
            cli_mod.main()
        except SystemExit as e:
            return int(e.code or 0)
    return 0


def test_workspaces_list_empty(tmp_config_dir: Path, capsys) -> None:
    _run_cli(["workspaces"])
    out = capsys.readouterr().out
    assert "no workspaces" in out.lower() or "no profiles" in out.lower()


def test_workspaces_list_shows_current(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1", "instance_name": "aarya"})
    pm.save("personal", {"relay_url": "u2", "instance_name": "aarya"})
    pm.set_current("work")

    _run_cli(["workspaces"])
    out = capsys.readouterr().out
    assert "work" in out
    assert "personal" in out
    # Current marker — implementation uses "*" or "(current)"
    assert "*" in out or "current" in out.lower()


def test_workspaces_use_switches_current(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u1"})
    pm.save("personal", {"relay_url": "u2"})
    pm.set_current("work")

    _run_cli(["workspaces", "use", "personal"])

    assert ProfileManager(config_dir=tmp_config_dir).current() == "personal"


def test_workspaces_use_unknown_errors(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})

    rc = _run_cli(["workspaces", "use", "ghost"])
    assert rc != 0
    err = capsys.readouterr().err + capsys.readouterr().out
    assert "not found" in err.lower() or "no such" in err.lower()


def test_workspaces_rm_deletes(tmp_config_dir: Path) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.save("personal", {"relay_url": "u"})
    pm.set_current("work")

    _run_cli(["workspaces", "rm", "personal", "--yes"])

    assert ProfileManager(config_dir=tmp_config_dir).list() == ["work"]


def test_workspaces_rm_refuses_last(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "u"})
    pm.set_current("work")

    rc = _run_cli(["workspaces", "rm", "work", "--yes"])
    assert rc != 0
    assert ProfileManager(config_dir=tmp_config_dir).list() == ["work"]


def test_whoami_shows_current(tmp_config_dir: Path, capsys) -> None:
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {
        "relay_url": "https://r.test",
        "instance_name": "aarya",
        "workspace_label": "Work",
    })
    pm.set_current("work")

    _run_cli(["whoami"])
    out = capsys.readouterr().out
    assert "aarya" in out
    assert "https://r.test" in out


def test_whoami_reports_none_when_unset(tmp_config_dir: Path, capsys) -> None:
    rc = _run_cli(["whoami"])
    out = capsys.readouterr().out
    assert "no current workspace" in out.lower() or rc != 0


def test_workspace_flag_overrides_current(tmp_config_dir: Path, monkeypatch) -> None:
    """--workspace flag selects the profile for one command."""
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test"})
    pm.save("personal", {"relay_url": "https://personal.test"})
    pm.set_current("work")

    # whoami with -w personal should report personal even though current is work.
    import io, contextlib
    from quorus_cli import cli as cli_mod
    buf = io.StringIO()
    with patch.object(cli_mod.sys, "argv",
                      ["quorus", "-w", "personal", "whoami"]):
        with contextlib.redirect_stdout(buf):
            try:
                cli_mod.main()
            except SystemExit:
                pass
    assert "https://personal.test" in buf.getvalue()
```

- [ ] **Step 3.2: Run, confirm failures**

```bash
pytest tests/test_cli_workspaces.py -v
```
Expected: failures — subcommands don't exist yet.

- [ ] **Step 3.3: Add -w/--workspace top-level flag**

In `packages/cli/quorus_cli/cli.py`, immediately after `sub = parser.add_subparsers(dest="command")` (line 5138), add:

```python
    parser.add_argument(
        "-w", "--workspace",
        dest="workspace",
        default=None,
        help=(
            "Select a specific workspace profile for this command. "
            "Priority: flag > QUORUS_PROFILE env > current pointer."
        ),
    )
```

Then add this helper near the top of `main()` — before the subparser block, after the `_help_block` helper is defined (around line 5170):

```python
    def _resolve_active_profile(args) -> str | None:
        """Return the profile slug to use for this command, or None for current."""
        if getattr(args, "workspace", None):
            return args.workspace
        return os.environ.get("QUORUS_PROFILE")
```

(Note: `os` is already imported at the top of cli.py; verify before adding.)

- [ ] **Step 3.4: Add the workspace subcommand parser**

After the existing `p_begin = sub.add_parser("begin"...)` block (around line 5605), insert:

```python
    p_workspaces = sub.add_parser("workspaces", **_help_block(
        synopsis="Manage Quorus workspace profiles.",
        description=(
            "List, switch, add, or remove workspace profiles. Each profile "
            "is a separate identity (relay URL + API key + instance name). "
            "Profiles are stored under ~/.quorus/profiles/<slug>.json."
        ),
        example="quorus workspaces use personal",
        help_text="Manage workspace profiles",
    ))
    ws_sub = p_workspaces.add_subparsers(dest="ws_action")
    ws_sub.add_parser("list", help="List all workspace profiles (default)")
    p_ws_use = ws_sub.add_parser("use", help="Switch to a workspace")
    p_ws_use.add_argument("slug", help="Profile slug to make current")
    p_ws_add = ws_sub.add_parser("add", help="Create a new workspace profile")
    p_ws_add.add_argument("name", nargs="?", default=None,
                          help="Optional slug for the new profile")
    p_ws_rm = ws_sub.add_parser("rm", help="Delete a workspace profile")
    p_ws_rm.add_argument("slug", help="Profile slug to delete")
    p_ws_rm.add_argument("--yes", action="store_true",
                         help="Skip confirmation")

    sub.add_parser("login", **_help_block(
        synopsis="Add a new workspace profile (alias for `workspaces add`).",
        description=(
            "Runs the first-launch wizard — sign up on the public relay, "
            "paste an invite token, or auto-detect a local relay — and "
            "saves the result as a new workspace profile."
        ),
        example="quorus login",
        help_text="Add a new workspace profile",
    ))

    sub.add_parser("whoami", **_help_block(
        synopsis="Show the current workspace profile's identity.",
        description=(
            "Prints the active profile's instance_name, workspace label, "
            "and relay URL."
        ),
        example="quorus whoami",
        help_text="Show active workspace identity",
    ))
```

- [ ] **Step 3.5: Add the handler functions**

At the bottom of `cli.py`, BEFORE `def main():` (line 5112), add:

```python
def _cmd_workspaces(args) -> None:
    """Dispatch `quorus workspaces [list|use|add|rm]`."""
    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    action = getattr(args, "ws_action", None) or "list"

    if action == "list":
        _workspaces_list(pm)
        return
    if action == "use":
        _workspaces_use(pm, args.slug)
        return
    if action == "add":
        _workspaces_add(pm, getattr(args, "name", None))
        return
    if action == "rm":
        _workspaces_rm(pm, args.slug, yes=bool(getattr(args, "yes", False)))
        return
    raise SystemExit(f"Unknown workspaces action: {action}")


def _workspaces_list(pm) -> None:
    slugs = pm.list()
    if not slugs:
        _ui.console.print(
            "  [dim]no workspaces yet — run [bold]quorus login[/] to add one[/]"
        )
        return
    current = pm.current()
    _ui.console.print("  [bold]Workspaces[/]")
    for slug in slugs:
        data = pm.get(slug) or {}
        label = data.get("workspace_label") or data.get("instance_name") or slug
        relay = data.get("relay_url", "")
        marker = "*" if slug == current else " "
        _ui.console.print(
            f"  {marker} [bold]{slug:<16}[/]  [dim]{label}  ·  {relay}[/]"
        )
    _ui.console.print("")
    _ui.console.print("  [dim]switch: quorus workspaces use <slug>[/]")


def _workspaces_use(pm, slug: str) -> None:
    try:
        pm.set_current(slug)
    except FileNotFoundError:
        _ui.console.print(f"  [red]No such workspace: {slug}[/]")
        raise SystemExit(4)
    except ValueError as e:
        _ui.console.print(f"  [red]{e}[/]")
        raise SystemExit(5)
    _ui.console.print(f"  [green]✓[/] now using workspace [bold]{slug}[/]")


def _workspaces_add(pm, desired_name: str | None) -> None:
    """Run the first-launch wizard and save as a new profile.

    Reuses the TUI's _first_launch_setup flow so all three paths
    (local autodetect / invite / signup) are available.
    """
    # Lazy import — hub.py pulls in heavy TUI deps.
    from quorus_tui.hub import _first_launch_setup
    cfg = _first_launch_setup(_ui.console)
    if not cfg:
        _ui.console.print("  [red]signup cancelled[/]")
        raise SystemExit(1)

    # Pick a slug: user-supplied, or derive from instance_name, then dedupe.
    base = (desired_name or cfg.get("instance_name") or "default").lower()
    # Sanitise to match ProfileManager's slug regex.
    import re
    base = re.sub(r"[^a-z0-9_-]+", "-", base).strip("-") or "default"
    slug = base
    existing = set(pm.list())
    i = 2
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1

    pm.save(slug, cfg)
    pm.set_current(slug)
    _ui.console.print(
        f"  [green]✓[/] workspace [bold]{slug}[/] created and active"
    )


def _workspaces_rm(pm, slug: str, *, yes: bool) -> None:
    existing = pm.list()
    if slug not in existing:
        _ui.console.print(f"  [red]No such workspace: {slug}[/]")
        raise SystemExit(4)
    if len(existing) == 1:
        _ui.console.print(
            f"  [red]Refusing to delete last workspace ({slug}).[/] "
            "Add another first: quorus login"
        )
        raise SystemExit(5)
    if not yes:
        _ui.console.print(
            f"  Delete workspace [bold]{slug}[/]? Pass --yes to confirm."
        )
        raise SystemExit(5)
    was_current = (pm.current() == slug)
    pm.delete(slug)
    msg = f"  [green]✓[/] deleted workspace [bold]{slug}[/]"
    if was_current:
        msg += "  [dim](no current workspace — pick one with `quorus workspaces use`)[/]"
    _ui.console.print(msg)


def _cmd_login(args) -> None:
    """Alias: quorus login == quorus workspaces add."""
    from quorus.profiles import ProfileManager
    _workspaces_add(ProfileManager(), getattr(args, "name", None))


def _cmd_whoami(args) -> None:
    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    # Honor --workspace / QUORUS_PROFILE override.
    override = getattr(args, "workspace", None) or os.environ.get("QUORUS_PROFILE")
    slug = override or pm.current()
    if slug is None:
        _ui.console.print(
            "  [dim]no current workspace — run "
            "[bold]quorus workspaces use <slug>[/][/]"
        )
        return
    data = pm.get(slug) or {}
    if not data:
        _ui.console.print(f"  [red]profile {slug!r} missing or corrupt[/]")
        raise SystemExit(1)
    label = data.get("workspace_label") or slug
    _ui.console.print(f"  [bold]@{data.get('instance_name', '?')}[/]")
    _ui.console.print(f"  [dim]workspace:[/] {label}  [dim]({slug})[/]")
    _ui.console.print(f"  [dim]relay:[/]     {data.get('relay_url', '?')}")
```

- [ ] **Step 3.6: Wire the commands into the dispatch table**

In the `commands = {...}` dict (line 5613), add these three entries (alphabetical-ish with neighbors):

```python
        "workspaces": _cmd_workspaces,
        "login": _cmd_login,
        "whoami": _cmd_whoami,
```

- [ ] **Step 3.7: Run CLI tests**

```bash
pytest tests/test_cli_workspaces.py -v
```
Expected: all green.

- [ ] **Step 3.8: Run the full existing CLI test suite**

```bash
pytest tests/test_cli.py tests/test_cli_swarm.py -v
```
Expected: all green — new subcommands and the `-w` flag must not break existing parses.

- [ ] **Step 3.9: Lint**

```bash
ruff check packages/cli/quorus_cli/cli.py tests/test_cli_workspaces.py
```
Expected: clean.

- [ ] **Step 3.10: Also update the MCP server to honor QUORUS_PROFILE**

Modify `packages/mcp/quorus_mcp/server.py`. Replace lines 14 and 23:

Replace:
```python
from quorus.config import load_config
```
with:
```python
from quorus.config import ConfigManager, load_config
```

Replace the block at lines 23–24:
```python
_config = load_config()
CONFIG_FILE = Path(_config["config_file"])
```
with:
```python
# Honor QUORUS_PROFILE env var: pick a specific profile without touching
# "current". Falls back to the current-profile pointer / legacy migration
# path inside load_config().
_profile_slug = os.environ.get("QUORUS_PROFILE")
if _profile_slug:
    # Load just the named profile's data, then let load_config() apply
    # env-var overrides on top (env still beats profile for direct
    # overrides like QUORUS_RELAY_URL).
    _profile_data = ConfigManager(profile=_profile_slug).load()
    # load_config() reads from resolve_config_file() — temporarily point
    # it at the profile file by setting QUORUS_CONFIG_DIR is too invasive,
    # so instead we build the config dict by hand using the same priority
    # rules load_config() uses (env > file > default).
    _config = {
        "config_file": str(ConfigManager(profile=_profile_slug).path),
        "relay_url": (
            os.environ.get("RELAY_URL")
            or _profile_data.get("relay_url", "http://localhost:8080")
        ),
        "relay_secret": (
            os.environ.get("RELAY_SECRET")
            or _profile_data.get("relay_secret", "")
        ),
        "api_key": (
            os.environ.get("API_KEY") or _profile_data.get("api_key", "")
        ),
        "instance_name": (
            os.environ.get("INSTANCE_NAME")
            or _profile_data.get("instance_name", "default")
        ),
        "enable_background_polling": True,
        "poll_mode": (
            (os.environ.get("POLL_MODE") or _profile_data.get("poll_mode") or "sse")
            .strip().lower()
        ),
        "push_notification_method": (
            os.environ.get("PUSH_NOTIFICATION_METHOD")
            or _profile_data.get("push_notification_method")
            or "notifications/claude/channel"
        ),
        "push_notification_channel": (
            os.environ.get("PUSH_NOTIFICATION_CHANNEL")
            or _profile_data.get("push_notification_channel", "quorus")
        ),
    }
    if _config["poll_mode"] not in {"lazy", "sse"}:
        _config["poll_mode"] = "sse"
else:
    _config = load_config()
CONFIG_FILE = Path(_config["config_file"])
```

- [ ] **Step 3.11: Add an MCP smoke test**

Append to `tests/test_cli_workspaces.py`:

```python
def test_mcp_server_honors_quorus_profile(
    tmp_config_dir: Path, monkeypatch
) -> None:
    """Loading quorus_mcp.server with QUORUS_PROFILE set should
    resolve identity from that profile, not the current pointer."""
    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("current-one", {"relay_url": "https://wrong.test", "api_key": "sk_x"})
    pm.save("target", {
        "relay_url": "https://right.test",
        "api_key": "sk_y",
        "instance_name": "targetbot",
    })
    pm.set_current("current-one")

    monkeypatch.setenv("QUORUS_PROFILE", "target")
    # Required so server-module import doesn't raise on "no auth".
    # (Our profile supplies api_key, so nothing to set here.)

    # Import fresh — use importlib.reload to force re-eval of module-level
    # _config computation.
    import importlib
    import quorus_mcp.server as server_mod
    importlib.reload(server_mod)

    assert server_mod.RELAY_URL == "https://right.test"
    assert server_mod.API_KEY == "sk_y"
    assert server_mod.INSTANCE_NAME == "targetbot"
```

- [ ] **Step 3.12: Run all tests, lint**

```bash
pytest tests/test_cli_workspaces.py tests/test_config_compat.py tests/test_profiles.py -v
ruff check packages/cli/quorus_cli/cli.py packages/mcp/quorus_mcp/server.py
```
Expected: all green, clean.

- [ ] **Step 3.13: Commit 3**

```bash
git add packages/cli/quorus_cli/cli.py packages/mcp/quorus_mcp/server.py tests/test_cli_workspaces.py
git commit -m "$(cat <<'EOF'
feat(cli): quorus workspaces, login, whoami + QUORUS_PROFILE

- quorus workspaces list|use|add|rm — manage profiles from the CLI.
- quorus login — alias for `workspaces add`, runs the first-launch
  wizard and saves the result as a new profile.
- quorus whoami — print the active profile's identity.
- -w/--workspace flag on every subcommand (one-shot override).
- MCP server honors QUORUS_PROFILE so different MCP clients can
  point at different workspaces via env.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Task 4: TUI live workspace switching

**Files:**
- Modify: `packages/tui/quorus_tui/hub.py`
- Test: `tests/test_tui_workspace_switch.py`

- [ ] **Step 4.1: Write failing test**

Create `tests/test_tui_workspace_switch.py`:

```python
"""TUI workspace switch lifecycle.

This test doesn't run the full interactive loop (that would require
a pty). It exercises the _run_session helper's early-return path when
a switch is requested via HubState.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_hubstate_request_switch_sets_pending(tmp_config_dir: Path) -> None:
    from quorus_tui.hub import HubState
    s = HubState()
    assert s.pending_workspace_switch() is None
    s.request_workspace_switch("personal")
    assert s.pending_workspace_switch() == "personal"


def test_run_session_returns_switch_tuple_on_request(
    tmp_config_dir: Path,
) -> None:
    """When state.request_workspace_switch() is set before the loop
    hits its next tick, _run_session tears down threads and returns
    ("switch", slug)."""
    from quorus_tui.hub import HubState, _run_session

    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {
        "relay_url": "https://work.test",
        "instance_name": "aarya",
        "api_key": "sk_w",
    })

    profile = pm.get("work")
    state = HubState()
    # Pre-seed the switch request so the main loop exits on first tick.
    state.request_workspace_switch("personal")

    # Patch out network-y helpers so the session setup doesn't hit real URLs.
    with patch("quorus_tui.hub._get_auth_token", return_value="fake-jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=[]), \
         patch("quorus_tui.hub._create_room", return_value=("ok", {})), \
         patch("quorus_tui.hub._poll_loop"), \
         patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._read_input",
               side_effect=lambda *a, **kw: ""):
        result = _run_session(profile, state=state)

    assert isinstance(result, tuple)
    assert result[0] == "switch"
    assert result[1] == "personal"


def test_run_session_returns_quit_on_quit_key(tmp_config_dir: Path) -> None:
    from quorus_tui.hub import HubState, _run_session, _KEY_QUIT

    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test", "api_key": "sk_w"})
    profile = pm.get("work")
    state = HubState()

    with patch("quorus_tui.hub._get_auth_token", return_value="fake-jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=[]), \
         patch("quorus_tui.hub._create_room", return_value=("ok", {})), \
         patch("quorus_tui.hub._poll_loop"), \
         patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._read_input", return_value=_KEY_QUIT):
        result = _run_session(profile, state=state)

    assert result == "quit"
```

- [ ] **Step 4.2: Run, confirm failure**

```bash
pytest tests/test_tui_workspace_switch.py -v
```
Expected: failures — `HubState.request_workspace_switch` doesn't exist, `_run_session` doesn't exist.

- [ ] **Step 4.3: Add HubState methods**

In `packages/tui/quorus_tui/hub.py`, find the `class HubState:` definition (around line 617). Add these methods inside the class (placement: near the other `set_status_bar` / `set_rooms` methods):

```python
    def request_workspace_switch(self, slug: str) -> None:
        """Signal the main loop to tear down and switch to *slug*."""
        with self._lock:
            self._pending_switch = slug

    def pending_workspace_switch(self) -> str | None:
        with self._lock:
            return getattr(self, "_pending_switch", None)

    def clear_workspace_switch(self) -> None:
        with self._lock:
            self._pending_switch = None
```

Also initialize `_pending_switch` in `HubState.__init__` — add this line alongside other field defaults:

```python
        self._pending_switch: str | None = None
```

- [ ] **Step 4.4: Refactor run_hub → outer loop + _run_session**

In `packages/tui/quorus_tui/hub.py`, replace the `run_hub()` function (starts at line 1609, ending where the existing try/except for the main loop ends — approximately line 2000+). Split into two functions.

First, find where the existing `run_hub` body ends. The main `while True:` loop is around line 1692, and it ends with a `try/except` around exit cleanup. Carefully preserve all logic — we're just moving it into `_run_session(profile, state=None)`.

Replace `def run_hub() -> None:` and its body with:

```python
def run_hub() -> None:
    """Outer loop: pick a profile, run a session, loop on switch."""
    console = _ui.console

    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    pm.migrate_legacy_if_needed()

    while True:
        profile = pm.current_profile()
        if profile is None:
            # Fresh install — fall through to wizard, save as default.
            profile = _first_launch_setup(console)
            if not profile:
                return
            pm.save("default", profile)
            pm.set_current("default")

        result = _run_session(profile)
        if result == "quit":
            return
        if isinstance(result, tuple) and result[0] == "switch":
            new_slug = result[1]
            try:
                pm.set_current(new_slug)
            except FileNotFoundError:
                console.print(
                    f"  [red]workspace {new_slug!r} vanished — "
                    "returning to current[/]"
                )
                continue
            # Loop continues — next iteration picks up the new profile.
            continue
        # Any other shape — treat as quit.
        return


def _run_session(
    profile: dict,
    *,
    state: "HubState | None" = None,
) -> "str | tuple[str, str]":
    """Run one workspace session. Returns 'quit' or ('switch', slug).

    Extracted from the old run_hub body. The *state* parameter exists so
    tests can pre-seed a HubState with a pending switch and observe the
    early-exit path without running the full interactive loop.
    """
    console = _ui.console

    relay_url: str = profile.get("relay_url", DEFAULT_RELAY).rstrip("/")
    agent_name: str = profile.get("instance_name", "agent")
    raw_secret: str = profile.get("relay_secret", "") or profile.get("api_key", "")
    workspace_label: str = profile.get("workspace_label") or ""

    secret = _get_auth_token(relay_url, raw_secret)

    console.print(f"\n  [dim]Connecting to {relay_url}...[/dim]")
    rooms = _fetch_rooms(relay_url, secret)
    if not rooms:
        starter = "general"
        status, _ = _create_room(relay_url, secret, starter, agent_name)
        if status == "ok":
            console.print(
                f"  [dim]Created starter room [room]#{starter}[/] — you can add more anytime.[/]"
            )
            rooms = _fetch_rooms(relay_url, secret)
        elif status == "conflict":
            rooms = _fetch_rooms(relay_url, secret)

    if state is None:
        state = HubState()
    state.set_rooms(rooms)
    if rooms:
        state.set_connected(True, "Connected")
        first_room = rooms[0].get("name") or rooms[0].get("id", "")
        if first_room:
            _join_room(relay_url, secret, first_room, agent_name)
            msgs = _fetch_history(relay_url, secret, first_room)
            if msgs is not None:
                state.set_messages(msgs)
    else:
        state.set_connected(False, "No rooms yet")

    stop_event = threading.Event()
    poller = threading.Thread(
        target=_poll_loop,
        args=(relay_url, secret, state, stop_event),
        daemon=True,
    )
    poller.start()
    sse_listener = threading.Thread(
        target=_sse_loop,
        args=(relay_url, secret, agent_name, state, console, stop_event),
        daemon=True,
    )
    sse_listener.start()

    try:
        return _main_input_loop(
            console=console,
            state=state,
            relay_url=relay_url,
            secret=secret,
            agent_name=agent_name,
            workspace_label=workspace_label,
        )
    finally:
        stop_event.set()
        poller.join(timeout=2)
        sse_listener.join(timeout=2)
```

- [ ] **Step 4.5: Extract the main interactive loop into _main_input_loop**

The existing `run_hub` had a huge `while True:` loop (roughly lines 1692 onward) that does rendering + input. Wrap that loop in a function:

Add a new function just before `_run_session`:

```python
def _main_input_loop(
    *,
    console,
    state: "HubState",
    relay_url: str,
    secret: str,
    agent_name: str,
    workspace_label: str = "",
) -> "str | tuple[str, str]":
    """The render+input loop. Returns 'quit' or ('switch', slug).

    This is the body of the old run_hub main loop, plus a per-tick
    check for pending workspace-switch requests set by /workspace.
    """
    console.clear()
    last_render = 0.0
    hold_render = False
    input_history: list[str] = []
    HISTORY_MAX = 100

    while True:
        # Pending workspace switch? Exit the loop so _run_session
        # can tear down threads and return ("switch", slug).
        pending = state.pending_workspace_switch()
        if pending is not None:
            state.clear_workspace_switch()
            return ("switch", pending)

        now = time.monotonic()

        if not hold_render and now - last_render >= POLL_S:
            # -- EXISTING RENDER BLOCK GOES HERE UNCHANGED --
            # (Copy lines ~1697–1803 of the old run_hub body verbatim,
            #  but pass workspace_label into _render_header — see
            #  step 4.6 for the header change.)
            ...

        line = _read_input("❯ ", history=input_history)
        hold_render = False
        state.reset_inline_rule()

        if line == _KEY_QUIT:
            return "quit"

        # -- EXISTING INPUT HANDLING GOES HERE UNCHANGED --
        # (Copy the rest of the while-body — Tab, number, bare /, slash
        #  dispatch, join/create/invite regex blocks, chat send.)
        ...
```

**Implementation note:** Rather than re-type hundreds of lines, the concrete work is:

1. Cut the entire `while True:` body from the old `run_hub` (from `while True:` at ~line 1692 down to its matching end).
2. Paste it verbatim into `_main_input_loop` as the body.
3. Add the `pending = state.pending_workspace_switch()` check at the very top of the `while True:` body.
4. Replace every `break` (which exited to quit) with `return "quit"`.
5. Replace the old `except (EOFError, KeyboardInterrupt):` bare fallthrough with `return "quit"`.
6. All variable captures (`relay_url`, `secret`, `agent_name`) come from function args — they're already named identically.

- [ ] **Step 4.6: Thread workspace_label into the header**

Find `_render_header` (line 1015). Add a parameter:

```python
def _render_header(
    relay_url: str,
    agent_name: str,
    connected: bool,
    status: str,
    *,
    room_count: int = 0,
    unread_total: int = 0,
    latency_ms: int | None = None,
    workspace_label: str = "",    # <-- new
) -> Text:
```

In the body (around where it formats the `@name · N rooms` line), insert the workspace after `@name`:

```python
    # Existing: line.append(f"@{agent_name}", ...)
    # After that, add:
    if workspace_label:
        line.append(f"  ·  ", style="dim")
        line.append(workspace_label, style="accent")
```

(Exact style token name depends on the existing theme palette — match an existing bright-ish token used in the header. `accent` and `primary` are both available per the `_ui.THEME` comment at line 1610.)

In `_main_input_loop`, update the call:
```python
                console.print(
                    _render_header(
                        relay_url,
                        agent_name,
                        connected,
                        conn_status,
                        room_count=len(rooms_snap),
                        unread_total=unread_total,
                        latency_ms=latency_ms,
                        workspace_label=workspace_label,
                    )
                )
```

- [ ] **Step 4.7: Add the /workspace slash command**

Find `_print_slash_hint` (line 1358) and `_slash_help` below it. Add new handler before `SLASH_COMMANDS = {...}` (around line 1461):

```python
def _print_workspace_menu(console) -> None:
    from quorus.profiles import ProfileManager
    pm = ProfileManager()
    slugs = pm.list()
    current = pm.current()
    console.print()
    console.print(Text.from_markup("  [bold primary]Workspaces[/]"))
    if not slugs:
        console.print(Text.from_markup(
            "  [dim]no workspaces — run [bold]quorus login[/] to add one[/]"
        ))
        console.print()
        return
    for slug in slugs:
        data = pm.get(slug) or {}
        label = (
            data.get("workspace_label")
            or data.get("instance_name")
            or slug
        )
        marker = "●" if slug == current else " "
        line = Text(f"  {marker}  ")
        if slug == current:
            line.append(slug, style="bold room")
            line.append("  active", style="dim success")
        else:
            line.append(slug, style="dim")
        line.append(f"  {label}", style="dim")
        console.print(line)
    console.print()
    console.print(Text.from_markup(
        "  [dim]switch: /workspace <slug>  ·  add: /workspace add[/]"
    ))
    console.print()


def _slash_workspace(arg, state, relay_url, secret, agent_name, console):
    del relay_url, secret, agent_name
    from quorus.profiles import ProfileManager
    target = arg.strip()
    pm = ProfileManager()

    if not target:
        _print_workspace_menu(console)
        return True

    if target == "add":
        # Deferred — can't run the wizard mid-TUI without tearing down
        # the render loop. Tell the user to use the CLI for now.
        state.set_status_bar(
            "run `quorus login` in a second terminal to add a workspace"
        )
        return True

    if pm.get(target) is None:
        state.set_status_bar(f"no such workspace: {target} — /workspace to list")
        return True

    # Signal the main loop to tear down and switch.
    state.request_workspace_switch(target)
    state.set_status_bar(f"switching to {target}...")
    return True
```

- [ ] **Step 4.8: Register /workspace in SLASH_COMMANDS**

Find the `SLASH_COMMANDS` dict (around line 1461). Add:

```python
    "/workspace": ("list / switch workspaces",           _slash_workspace),
```

Also add `/workspace` to the `printing_verbs` set inside `_main_input_loop` (it prints the menu when called with no args):

```python
                printing_verbs = {"/help", "/rooms", "/workspace"}
```

- [ ] **Step 4.9: Update /help to mention /workspace**

Find `_print_help` (line 1276). Add an entry to `slash_rows`:

```python
        ("/workspace",       "list or switch workspaces"),
```

(Insert right after `"/invite"` so related commands cluster together.)

- [ ] **Step 4.10: Run TUI tests**

```bash
pytest tests/test_tui_workspace_switch.py -v
```
Expected: all green.

- [ ] **Step 4.11: Run all tests**

```bash
pytest -v
```
Expected: all green. If existing TUI tests fail because of the `run_hub` refactor, fix them to exercise `_run_session` or `_main_input_loop` instead.

- [ ] **Step 4.12: Lint and syntax check**

```bash
ruff check packages/tui/quorus_tui/hub.py tests/test_tui_workspace_switch.py
python -c "import py_compile; py_compile.compile('packages/tui/quorus_tui/hub.py', doraise=True); print('ok')"
```
Expected: clean, "ok".

- [ ] **Step 4.13: Hot-patch pipx venv for manual smoke**

```bash
cp packages/tui/quorus_tui/hub.py ~/.local/pipx/venvs/quorus/lib/python3.14/site-packages/quorus_tui/hub.py
```

- [ ] **Step 4.14: Manual smoke test (user walks this)**

1. `quorus workspaces` — should list `default` (migrated from legacy).
2. `quorus login` — create a second profile.
3. `quorus workspaces` — shows both, `default` marked current.
4. `quorus whoami` — prints identity.
5. `quorus` — TUI opens, header shows workspace label.
6. `/workspace` in TUI — shows menu.
7. `/workspace <other-slug>` — screen tears down, new session starts, header updates.
8. `/quit` — exits cleanly.

- [ ] **Step 4.15: Commit 4**

```bash
git add packages/tui/quorus_tui/hub.py tests/test_tui_workspace_switch.py
git commit -m "$(cat <<'EOF'
feat(tui): live workspace switching

- run_hub() is now an outer loop over _run_session(profile).
- New HubState.request_workspace_switch(slug) signals the input
  loop to exit cleanly.
- /workspace slash command lists profiles; /workspace <slug>
  tears down SSE + poll threads (2s join), returns to run_hub,
  which re-enters _run_session with the new profile.
- Header shows the active workspace label beside @name.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Verification After All Four Commits

- [ ] **Full test run**

```bash
pytest -v
```
Expected: all green.

- [ ] **Full lint**

```bash
ruff check .
```
Expected: clean.

- [ ] **Manual end-to-end (as the user)**

Follow Step 4.14.

- [ ] **Update CONTEXT.md**

Append to `CONTEXT.md` per project convention:
- Recent changes: "Multi-workspace support landed — profiles under `~/.quorus/profiles/`, `/workspace` slash command, `quorus workspaces|login|whoami`."
- Current state: Note the new capability.
- Drop oldest entry from Recent Changes to keep it at 10.

---

## Self-Review (done before user sees this plan)

- **Spec coverage:** Every section of the spec maps to a task:
  - ProfileManager → Task 1 ✓
  - ConfigManager facade → Task 2 ✓
  - Legacy migration → Task 1 (method) + Task 2 (auto-trigger) ✓
  - CLI `workspaces/login/whoami` → Task 3 ✓
  - `-w/--workspace` flag → Task 3.3 ✓
  - MCP `QUORUS_PROFILE` → Task 3.10 ✓
  - TUI live switch → Task 4 ✓
  - Header workspace label → Task 4.6 ✓
  - `/workspace` command → Task 4.7–4.8 ✓
  - Error handling (corrupt file, missing pointer, slug collision, delete current, thread join timeout) → covered in Tasks 1/3/4 with tests ✓
  - Tests listed in spec → `test_profiles.py`, `test_config_compat.py`, `test_cli_workspaces.py` (spec says `test_tui_workspace_switch.py`) — all present ✓

- **Placeholder scan:** The only abbreviated section is the verbatim copy of the old `run_hub` body into `_main_input_loop` (Step 4.5). This is intentional — the block is hundreds of lines and already exists in the codebase. The step gives a precise procedure (cut from line 1692, paste into new function, replace `break` with `return "quit"`, add pending-switch check). Not a placeholder — it's a surgical refactor.

- **Type consistency:** `ProfileManager` methods use consistent signatures across Tasks 1/2/3/4. `HubState.request_workspace_switch(slug)` / `pending_workspace_switch()` / `clear_workspace_switch()` names match everywhere. `_run_session` return type `"quit" | ("switch", slug)` is consistent in tests (Task 4.1) and implementation (Task 4.4–4.5).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-multi-workspace-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints.

Which approach?
