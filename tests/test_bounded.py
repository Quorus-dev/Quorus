"""quorus.bounded: one range check for untrusted numbers, and every site that uses it.

Overseer, 2026-10-21: the second 10**400 OverflowError in a week (limit_status on
70083186, check_config on 2921ac4e). One helper, tested once here, plus a sweep
proving each call site rejects the huge values instead of raising.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from quorus.bounded import bounded

HUGE = 10 ** 400  # valid JSON; float() and math.isfinite() of it raise OverflowError
ROOT = Path(__file__).resolve().parents[1]


def test_bounded() -> None:
    assert bounded(5, 0, 10) == 5.0 and isinstance(bounded(5, 0, 10), float)
    assert bounded(2.5, 0, 10) == 2.5
    assert bounded(0, 0, 10) == 0.0 and bounded(10, 0, 10) == 10.0  # inclusive
    for bad in (HUGE, -HUGE, 11, -1, 10.5, float("nan"), float("inf"), float("-inf"),
                True, False, None, "5", [5], {"v": 5}):
        assert bounded(bad, 0, 10) is None, bad
    assert bounded(HUGE, 0, float("inf")) is None  # unbounded above: still no overflow
    assert bounded(1e308, 0, float("inf")) == 1e308 and bounded(10 ** 300, 0, 1e301) == 1e300


# ── every call site rejects huge numbers without raising ────────────────


def _reflexd_limits():
    spec = importlib.util.spec_from_file_location("reflexd_limits_sweep",
                                                  ROOT / "scripts" / "reflexd_limits.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("huge", [HUGE, -HUGE])
def test_every_site_rejects_huge_numbers_without_raising(huge: int) -> None:
    from quorus import limit_status
    from quorus.protocol.handoff_capsule import LimitContext
    from quorus.runtime.check_config import CheckConfig

    now = 1_800_000_000.0
    # limit_status: pause and reading epochs, used_pct
    assert limit_status.clean({"reason": "quota", "until": huge}, [], now) == {}
    assert limit_status.clean(None, [{"window": "5h", "kind": "reported",
                                      "source": "claude.stream", "used_pct": huge,
                                      "seen_at": now}], now) == {}
    # check_config: timeout and output cap
    for field in ("timeout_s", "max_output_bytes"):
        with pytest.raises(ValueError):
            CheckConfig.from_record({"argv": ["pytest"], "cwd": ".", "timeout_s": 45.0,
                                     "max_output_bytes": 65536, field: huge})
    # handoff capsule: presence epochs before datetime conversion
    with pytest.raises(ValueError):
        LimitContext.from_presence({"window": "limit", "kind": "observed",
                                    "source": "msg.retry_in", "used_pct": None,
                                    "seen_at": huge, "resets_at": None},
                                   vendor="claude", reason="quota")
    # reflexd_limits: Claude's utilization and resetsAt
    limits = _reflexd_limits()
    event = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": huge}, "seven_day": {"utilization": 0.5,
                                                          "resetsAt": huge}}}}
    usage = limits.read_claude_usage(json.dumps(event), now)
    assert set(usage) == {"7d"} and usage["7d"].resets_at is None
