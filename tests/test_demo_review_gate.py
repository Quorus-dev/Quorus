"""`quorus demo` with the review gate (review-gate.md slice 4, the demo half): the
builder's fix is held off main until the reviewer's GO, and the demo passes only when
the GO came first. A live run on this machine: a private relay, two reflexd daemons on
stub opencode CLIs (tests/firstrun/stub_agents.py), the real hold, GO and publish paths."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from quorus_cli import demo_cmd
from rich.console import Console

from tests.firstrun import stub_agents

pytestmark = [pytest.mark.firstrun,
              pytest.mark.skipif(sys.platform == "win32", reason="POSIX daemons")]


def test_demo_lands_the_fix_only_after_the_reviewers_go(tmp_path: Path, monkeypatch) -> None:
    stubs = tmp_path / "stubs"
    _repo, bin_dir = stub_agents.setup(stubs, tools=("opencode",), owner=demo_cmd.HUMAN,
                                       reviewer=demo_cmd.REVIEWER)
    monkeypatch.setenv("PATH", ":".join([str(bin_dir), str(Path(sys.executable).parent),
                                         "/usr/bin", "/bin"]))
    monkeypatch.delenv("QUORUS_CONFIG_DIR", raising=False)
    out = io.StringIO()
    args = SimpleNamespace(model="stub/free", keep=True, timeout=240, use_my_login=False)
    root: Path | None = None
    try:
        demo_cmd.cmd_demo(args, Console(file=out, width=200, force_terminal=False))
        text = out.getvalue()
        kept = re.search(r"kept the demo folder: (\S+)", text)
        assert kept, text
        root = Path(kept.group(1))
        # The order the room saw: the hold, the reviewer's GO, then the reviewed publish.
        held = text.index(f"awaiting @{demo_cmd.REVIEWER}'s review of")
        go = text.index("GO on", held)
        published = text.index(f"reviewed by @{demo_cmd.REVIEWER}", go)
        assert held < go < published, text
        assert "after the review, not before" in text, text
        # The gate was real: a pinned reviewer in the daemons' config dir, keys per agent.
        pins = json.loads((root / "home" / ".quorus" / "room-reviewers.json").read_text())
        assert pins[demo_cmd.ROOM]["name"] == demo_cmd.REVIEWER
        assert pins[demo_cmd.ROOM]["claim_id"] and pins[demo_cmd.ROOM]["tenant"] == "_legacy"
        for name in (demo_cmd.BUILDER, demo_cmd.REVIEWER):
            keys = json.loads((demo_cmd.agent_key_dir(root / "home", name)
                               / "participant_keys.json").read_text())
            assert any(k.endswith("|" + name) for k in keys), name
        # Nothing printed the relay secret or a key.
        assert "quorus_pk_" not in text
    finally:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
