"""One way to read a number from untrusted JSON.

JSON allows integers of any size, and ``float(10**400)`` or ``math.isfinite(10**400)``
raises OverflowError instead of failing a range check. That broke two validators
in one week (limit_status on 70083186, check_config on 2921ac4e). Every check of
an untrusted number goes through ``bounded``: ints are compared as ints first,
and only an in-range value is ever converted.
"""

from __future__ import annotations

import math
import sys
from typing import Any


def bounded(value: Any, lo: float, hi: float) -> float | None:
    """``value`` as a finite float in ``[lo, hi]``, or None. Never raises.

    Bools are not numbers here (``True`` would otherwise read as 1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        # Exact int comparisons, before float(). The float-max bound matters when
        # hi is inf: 10**400 <= inf, yet float(10**400) still overflows.
        if lo <= value <= hi and -sys.float_info.max <= value <= sys.float_info.max:
            return float(value)
        return None
    return value if math.isfinite(value) and lo <= value <= hi else None
