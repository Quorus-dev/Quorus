"""The design loop's record keeper (scripts/site_evals.py): summary and lock only.

No Chrome, no Lighthouse, no relay here: those are the heavy, lock-held parts of
the real run. These tests pin what a release record contains and that the run
asks for the suite lock the way quorus-suite does.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "site_evals.py"


def _load():
    spec = importlib.util.spec_from_file_location("site_evals", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _lighthouse(perf: float, lcp: float, cls: float, tbt: float) -> dict:
    return {
        "categories": {
            "performance": {"score": perf},
            "accessibility": {"score": 1},
            "best-practices": {"score": 1},
            "seo": {"score": 1},
        },
        "audits": {
            "largest-contentful-paint": {"numericValue": lcp},
            "cumulative-layout-shift": {"numericValue": cls},
            "total-blocking-time": {"numericValue": tbt},
            "first-contentful-paint": {"numericValue": lcp / 2},
        },
    }


def test_summary_records_site_and_quorum_numbers_without_gating() -> None:
    m = _load()
    lh = {"mobile": _lighthouse(0.99, 1951.4, 0.0, 0), "desktop": _lighthouse(1.0, 421, 0.0, 0)}
    fps = {
        "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)",
        "samples": [120, 119, 121],
        "min": 119,
        "median": 120,
        "max": 121,
        "dpr": 2,
        "quality": "full",
        "width": 1532,
        "height": 1566,
        "tags": 6,
    }
    s = m.summarize("0.6.0", lh, fps, {"site-1440": {"out": "x.png"}}, {"index-abc.js": 3200})
    assert s["mode"] == "shadow" and s["label"] == "0.6.0"
    assert s["site"]["mobile"]["scores"] == {
        "performance": 99,
        "accessibility": 100,
        "best-practices": 100,
        "seo": 100,
    }
    assert s["site"]["mobile"]["lcp_ms"] == 1951.4 and s["site"]["desktop"]["cls"] == 0.0
    assert (
        s["quorum"]["median"] == 120
        and s["quorum"]["quality"] == "full"
        and s["quorum"]["dpr"] == 2
    )
    # Thresholds travel with the record but nothing fails on them (record-only, two releases).
    assert s["thresholds"] == {"lighthouse_min": 99, "fps_min": 60, "cls_max": 0.0}
    json.dumps(s)  # serialisable as written


def test_markdown_names_every_number_a_reader_needs() -> None:
    m = _load()
    s = m.summarize(
        "0.6.0",
        {"desktop": _lighthouse(1.0, 421, 0.0, 0)},
        {
            "renderer": "r",
            "samples": [113],
            "min": 113,
            "median": 113,
            "max": 113,
            "dpr": 2,
            "quality": "full",
            "width": 1532,
            "height": 1566,
            "tags": 7,
        },
        {},
        {},
    )
    md = m.markdown(s)
    assert "Lighthouse desktop: performance 100" in md and "LCP 421 ms" in md
    assert "min 113, median 113" in md and "DPR 2, quality full" in md
    assert "Nothing fails on them yet" in md


def test_run_takes_the_suite_lock_like_quorus_suite() -> None:
    m = _load()
    cmd = m.lock_command(["python", "scripts/site_evals.py"])
    assert cmd[:4] == ["/usr/bin/lockf", "-k", "-t", "1800"]
    assert cmd[4] == "/tmp/quorus-full-suite.lock" and cmd[5:] == [
        "python",
        "scripts/site_evals.py",
    ]


def test_demo_scene_is_the_recording_not_the_live_room() -> None:
    """The seeded relay is a fresh demo room with its own secret, never the user's relay."""
    m = _load()
    names = [n for n, _ in m.DEMO_MEMBERS]
    assert names[0] == "maya" and all(n.startswith("maya") for n in names)
    assert any("(quorus) published" in text for _, text in m.DEMO_LINES)
    assert any("@maya-claude" in text for _, text in m.DEMO_LINES)
    src = SCRIPT.read_text()
    assert (
        "secrets.token_urlsafe" in src and "self.proc.terminate()" in src
    )  # own secret, stopped by PID
    assert "pkill" not in src
