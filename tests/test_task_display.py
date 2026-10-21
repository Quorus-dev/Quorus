"""Task cells use the frozen task identity, never commit volume or rescuer identity."""
from quorus.runtime import task_display as display

NOW = 1800000000


def task(i, **changes):
    row = dict(task=str(i), agent="a-codex", model="m1", version="v1", kind="fix",
               claimed_at=NOW-10, outcome="kept", commits_published=5, commits_kept=5,
               approved=False, wall_s=60, tokens=100, unknown_usage=False)
    return row | changes


def test_sparse_cells_are_never_pooled_and_stale_tasks_disappear():
    rows = [task(i) for i in range(4)]
    rows += [task("model", model="m2"), task("version", version="v2"), task("kind", kind="test"),
             task("old", claimed_at=NOW-display.WINDOW_S-1)]
    cells = display.cells(rows, NOW)
    assert len(cells) == 4
    assert all(c["rate"] is None and c["interval"] is None for c in cells)
    assert all("not enough history" in c["summary"] for c in cells)
    assert sum(c["tasks"] for c in cells) == 7


def test_unknown_pending_do_not_enter_rate_and_tasks_not_commits_count():
    rows = [task(i) for i in range(5)]
    rows += [task("p", outcome="pending"), task("u", outcome="unknown"), task(0)]
    cell, = display.cells(rows, NOW)
    assert (cell["settled"], cell["kept"], cell["pending"], cell["unknown"]) == (5, 5, 1, 1)
    assert cell["commits_published"] == 35
    assert cell["rate"] == 1
    low, high = cell["interval"]
    assert .56 < low < .57 and .999 < high <= 1
    assert "1 pending, 1 unknown" in cell["summary"]


def test_all_outcomes_and_rescue_usage_remain_visible():
    rows = [task(i, outcome=o) for i, o in enumerate(display.OUTCOMES)]
    rows[0].update(wall_s=180, tokens=500, unknown_usage=True)
    cell, = display.cells(rows, NOW)
    assert all(cell[o] == 1 for o in display.OUTCOMES)
    assert cell["settled"] == 6
    assert cell["wall_s"] == 480 and cell["tokens"] == 1000
    assert cell["unknown_usage"] == 1
    text = display.line(rows, now=NOW)
    assert display.LABEL in text and "tokens unknown for 1 of 6" in text
    assert not any(word in text for word in ("better", "wins", "best"))


def test_empty_and_filtered_notices_keep_uncertainty():
    assert display.LABEL in display.notice([], "fix")
    text = display.line([task(1)], kind="docs", now=NOW)
    assert "0 pending, 0 unknown" in text and "not enough history (0)" in text


def test_unknown_time_and_no_attempt_tokens_are_not_displayed_as_safe_zero():
    rows = [task(1, tokens=None, wall_s=0, duration_unknown=1),
            task(2, tokens=0, wall_s=0, attempt_count=0)]
    cell, = display.cells(rows, NOW)
    assert cell["unknown_usage"] == cell["duration_unknown"] == 2
    assert "tokens unknown for 2 of 2" in cell["summary"]
    assert "time unknown for 2 of 2" in cell["summary"]
    assert "known tokens per kept" not in cell["summary"]
    assert "min per kept" not in cell["summary"]


def test_a_reported_zero_token_measurement_remains_distinct_from_unknown():
    cell, = display.cells([task(1, tokens=0, wall_s=0, attempt_count=1)], NOW)
    assert cell["unknown_usage"] == cell["duration_unknown"] == 0
    assert "0 known tokens per kept" in cell["summary"]
