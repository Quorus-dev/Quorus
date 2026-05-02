# Hackathon Cold-Install Regression — April 23, 2026

> tl;dr — `pip install -e .` inside a `uv venv` on Python 3.14 silently
> produces a venv where `import quorus_sdk` fails. The five packages are
> installed correctly on disk, but Python 3.14's `site.py` skips
> hatchling's `_editable_impl_quorus*.pth` files because uv flags every
> file in its venvs as macOS-hidden (`UF_HIDDEN`). The CLI launches, the
> root `quorus` package imports, then crashes one stack frame later when
> the SDK shim tries to forward to the non-importable `quorus_sdk`
> subpackage. The demo died at `quorus init`.

---

## Failure mode

```
$ pipx run --python python3.14 quorus --help
Traceback (most recent call last):
  File ".../quorus/__init__.py", line 14, in <module>
    from quorus.sdk import Room
  File ".../quorus/sdk.py", line 8, in <module>
    from quorus_sdk.http_agent import ReceiveResult
ModuleNotFoundError: No module named 'quorus_sdk'
```

Same shape inside a developer environment:

```
$ uv venv --python 3.14
$ source .venv/bin/activate
$ pip install -e .
... Successfully installed quorus-0.4.0
$ python -c "import quorus"
ModuleNotFoundError: No module named 'quorus_sdk'
```

The venv looks fine to the eye:

```
$ pip list | grep quorus
quorus      0.4.0  /path/to/repo
quorus-cli  0.4.0  /path/to/repo/packages/cli
quorus-mcp  0.4.0  /path/to/repo/packages/mcp
quorus-sdk  0.4.0  /path/to/repo/packages/sdk
quorus-tui  0.4.0  /path/to/repo/packages/tui
```

But:

```
$ python -c "import sys; print([p for p in sys.path if 'packages' in p])"
[]
```

Hatchling wrote five `.pth` files into site-packages — Python ignored them.

---

## Root cause

Three layered preconditions:

### 1. The repo is a hatchling monorepo

Root `pyproject.toml` declares:

```toml
[tool.hatch.build.targets.wheel]
packages = [
  "quorus",
  "packages/sdk/quorus_sdk",
  "packages/tui/quorus_tui",
  "packages/mcp/quorus_mcp",
  "packages/cli/quorus_cli",
]
```

For an **editable** install of the root project, hatchling drops a single
`.pth` file at `site-packages/_editable_impl_quorus.pth` that lists every
package's parent directory:

```
/repo
/repo/packages/cli
/repo/packages/mcp
/repo/packages/sdk
/repo/packages/tui
```

Python's `site.py` is supposed to read each `.pth` file and insert each
line into `sys.path`. That is the _only_ mechanism making
`import quorus_sdk` work after `pip install -e .`.

Importantly, `quorus/__init__.py` has a hard runtime dependency on this
working — `quorus/sdk.py` is a thin shim that imports from `quorus_sdk`,
so the moment the .pth file is ignored, every entry point that touches
`quorus.Room` (or anything that pulls in `quorus/__init__.py`) explodes.

### 2. Python 3.14 added "skip hidden .pth files"

CPython 3.14 / `site.addpackage()`:

```python
# Lib/site.py
if ((getattr(st, 'st_flags', 0) & stat.UF_HIDDEN) or
    (getattr(st, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_HIDDEN)):
    _trace(f"Skipping hidden .pth file: {fullname!r}")
    return
```

If the macOS `UF_HIDDEN` flag is set on a `.pth` file, Python now silently
ignores it. No warning, no error. Earlier Python versions (3.13 and below)
load every `.pth` file regardless of filesystem flags.

### 3. uv marks every file in its venvs as macOS-hidden

`uv venv` (and to a lesser extent `uv sync`) sets `UF_HIDDEN` on the
contents of `.venv/lib/.../site-packages/*` so Spotlight and Finder skip
indexing them. This is harmless on Python 3.13 and earlier — and actively
helpful for `find` performance — but on 3.14 it nukes editable installs
because hatchling's editable shim is implemented as a `.pth` file.

`python -m venv` (stdlib) does NOT set this flag. That's why a fresh
`/opt/homebrew/bin/python3.14 -m venv` followed by `pip install -e .`
works correctly — the .pth files are written without the hidden flag and
site.py loads them.

### Evidence

```
$ ls -lO .venv-from-uv/lib/python3.14/site-packages/_editable_impl_quorus.pth
-rw-r--r--@ 1 user staff hidden 426 May  2 00:00 _editable_impl_quorus.pth
                              ^^^^^^ UF_HIDDEN flag set by uv

$ python3.14 -v 2>&1 | grep _editable_impl_quorus
Skipping hidden .pth file: '...site-packages/_editable_impl_quorus.pth'

$ chflags nohidden .venv-from-uv/lib/python3.14/site-packages/_editable_impl_*.pth
$ python -c "import quorus; import quorus_sdk; print('ok')"
ok
```

```
$ ls -lO .venv-from-stdlib/lib/python3.14/site-packages/_editable_impl_quorus.pth
-rw-r--r--@ 1 user wheel - 426 May  2 00:02 _editable_impl_quorus.pth
                          ^^ no flag

$ python -c "import quorus; import quorus_sdk; print('ok')"
ok
```

---

## Why it broke at the hackathon and not before

| Variable                | Before Apr 23               | At Apr 23                |
| ----------------------- | --------------------------- | ------------------------ |
| Python on demo machine  | 3.13.x (homebrew)           | 3.14.3 (uv-managed)      |
| Venv tool               | `python -m venv`            | `uv venv`                |
| Repo layout             | monorepo (pre-existing)     | monorepo (pre-existing)  |
| Hatchling editable .pth | written without hidden flag | **written hidden by uv** |
| `import quorus_sdk`     | works                       | **silently fails**       |

Both Python 3.14 and uv-marking-files-hidden existed independently before.
The hackathon was the first time the demo machine combined them, and the
combination is fatal.

---

## Fix

### 1. Document the supported install path (this commit)

`setup.sh --install [pythonX.Y]` provisions a `.venv` at the repo root via
`python -m venv` (NOT `uv venv`), pip-installs the root in editable mode,
and runs `chflags nohidden site-packages/_editable_impl_*.pth` as a belt-
and-braces guard so the venv survives a future `uv sync` that might
restore the hidden flag.

### 2. Verify topology in CI (this commit)

`tests/test_install_topology.py` asserts:

- All 5 packages import cleanly.
- `quorus_cli.cli:main` is callable.
- The wheel manifest in root `pyproject.toml` still includes every
  subpackage source root.

If any of those break, the tests fail loudly and we never ship a wheel
that pipx can't install.

### 3. Wheel builds are unaffected

`python -m build --wheel` produces a single wheel containing all 5
packages (verified with `unzip -l dist/quorus-0.4.0-py3-none-any.whl`).
End users running `pipx install quorus` get a non-editable install — no
.pth files, no hidden-flag exposure — so this regression never reaches
PyPI users. It only bites contributors using `uv venv + pip install -e .`
on Python 3.14.

### 4. Python 3.14 support

We keep `requires-python = ">=3.10"`. 3.14 itself is fine; the regression
is in the _interaction_ between uv's hide-files behavior and CPython
3.14's hidden-.pth gate. `setup.sh --install` defends against it. We
verified clean editable installs work on Python 3.10, 3.11, 3.12, 3.13,
and 3.14 when the venv is created via `python -m venv` (stdlib).

If you must use `uv venv` on Python 3.14 for some reason:

```
uv venv --python 3.14
chflags nohidden -R .venv/lib/python3.14/site-packages/
uv pip install -e .
```

The `chflags -R nohidden` clears the hidden bit on every file in the
venv. Future `uv sync` runs will re-set it; either repeat the chflags
call or move to stdlib `python -m venv`.

---

## Aftermath / future-proofing

- `tests/test_install_topology.py` runs on every PR and asserts the
  invariant we lost.
- `setup.sh --install` is the supported developer onboarding path.
- The four pre-existing test failures (`test_resolve_config_file_legacy_fallback`,
  `test_resolve_config_file_prefers_default_over_legacy`,
  `test_file_persistence_save_and_load`, and
  `test_analytics_and_participants_persistence`) are independent of this
  regression and are tracked separately.
- If hatchling ever changes its editable filename from
  `_editable_impl_<pkg>.pth` to something not starting with an underscore,
  `setup.sh --install`'s glob still picks it up (it also globs `*.pth`).
