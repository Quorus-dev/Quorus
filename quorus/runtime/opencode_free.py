"""opencode's free models, read from the live catalog instead of a fixed list.

The free catalog churns: on release day every model in our hardcoded tuple was
rate-limited (or renamed) while another free model, nemotron-3.5-lightning-free,
answered (overseer's release check, 2026-10-17). The wake daemon and `quorus
demo` both rotate over this list. Known-good models from the fallback tuple go
first, in their order; any other live ``opencode/*-free`` model follows. If
``opencode models`` fails or lists none, the tuple is used as is.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence

CACHE_S = 60 * 60
_default_run = subprocess.run  # tests swap this so they never call a real opencode
_cache: tuple[float, tuple[str, ...]] | None = None


def _live(run: Callable[..., subprocess.CompletedProcess]) -> list[str]:
    try:
        out = run(["opencode", "models"], capture_output=True, text=True, timeout=20,
                  stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in (out.stdout or "").splitlines()
            if line.strip().startswith("opencode/") and line.strip().endswith("-free")]


def free_models(fallback: Sequence[str], *,
                run: Callable[..., subprocess.CompletedProcess] | None = None,
                now: Callable[[], float] = time.time) -> tuple[str, ...]:
    """The free models to rotate over, cached for an hour per process."""
    global _cache
    if _cache is not None and now() - _cache[0] < CACHE_S:
        return _cache[1]
    live = _live(run or _default_run)
    if live:
        known = [m for m in fallback if m in live]
        models = tuple(known + [m for m in live if m not in known])
    else:
        models = tuple(fallback)
    _cache = (now(), models)
    return models


def reset_cache() -> None:
    global _cache
    _cache = None
