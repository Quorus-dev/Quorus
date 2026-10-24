"""opencode's free models, read from the live catalog instead of a fixed list.

The free catalog churns: on release day every model in our hardcoded tuple was
rate-limited (or renamed) while another free model, nemotron-3.5-lightning-free,
answered (overseer's release check, 2026-10-17). The wake daemon and `quorus
demo` both rotate over this list. Known-good models from the fallback tuple go
first, in their order; any other live ``opencode/*-free`` model follows. If
``opencode models`` fails or lists none, the tuple is used as is.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence

# opencode's free tier is limited per account and network, shared across all its
# free models (its maintainers' bot on every October 2026 issue), and the 429 body is
# {"type":"error","error":{"type":"FreeUsageLimitError","message":"Free usage limit
# reached. ..."}} or the older "Rate limit exceeded. Please try again later."
# (docs/plans/keyless-answer-research.md). Trying the next free model after one of
# these only spends more of the same budget.
FREE_LIMIT_RE = re.compile(r"FreeUsageLimitError|usage limit|rate.?limit|quota|"
                           r"resource_exhausted", re.IGNORECASE)


def free_limit_in(stdout: str) -> str | None:
    """The typed free-tier limit in an ``opencode run --format json`` stream: the
    error event's own words, or None. Only ``"type":"error"`` events count, so a
    reply that merely talks about quotas never trips it."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "error":
            continue
        err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        text = " ".join(str(v) for v in (err.get("type"), err.get("name"), err.get("message"),
                                         data.get("message")) if v)
        if FREE_LIMIT_RE.search(text):
            return text[:300]
    return None

CACHE_S = 60 * 60
# A model that sat silent past the first-output deadline goes to the back of
# the rotation for this long (nemotron: 111-300s to its first token).
SLOW_FOR_S = 60 * 60
_slow: dict[str, float] = {}  # model -> until (per process)
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
    """The free models to rotate over, cached for an hour per process.
    Recently slow models come last, in their usual order."""
    global _cache
    if _cache is None or now() - _cache[0] >= CACHE_S:
        live = _live(run or _default_run)
        if live:
            known = [m for m in fallback if m in live]
            models = tuple(known + [m for m in live if m not in known])
        else:
            models = tuple(fallback)
        _cache = (now(), models)
    t = now()
    return tuple(sorted(_cache[1], key=lambda m: _slow.get(m, 0.0) > t))


def mark_slow(model: str | None, *, now: Callable[[], float] = time.time) -> None:
    if model:
        _slow[model] = now() + SLOW_FOR_S


def is_slow(model: str | None, *, now: Callable[[], float] = time.time) -> bool:
    return bool(model) and _slow.get(model or "", 0.0) > now()


def reset_cache() -> None:
    global _cache
    _cache = None
    _slow.clear()
