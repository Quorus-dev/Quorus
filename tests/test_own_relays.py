"""The own-relay record stands alone: H4's "is this my relay?" check (agent_cmd) and
the relay's own record (relay.record_own_relay) need it without any signing code.
Ported for 0.6.5 from 9971eeb7 and test_owner_sig_daemon.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from quorus.runtime import own_relays


def test_own_relays_imports_no_signing_code() -> None:
    tree = ast.parse(Path(own_relays.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any("sig" in n for n in names), names


def test_a_recorded_relay_reads_back_normalized(tmp_path: Path) -> None:
    path = tmp_path / "own_relays.json"
    own_relays.add_own_relay(path, "http://127.0.0.1:8787/")
    assert own_relays.own_relays(path) == {"http://127.0.0.1:8787"}


@pytest.mark.parametrize("bound,expected", [
    ([("127.0.0.1", 8787)], {"http://127.0.0.1:8787"}),
    ([("127.0.0.1", 8787), ("::1", 8787)],
     {"http://127.0.0.1:8787", "http://[::1]:8787", "http://localhost:8787"}),
    ([("0.0.0.0", 8787)], set()),  # wildcard: a specific loopback bind elsewhere wins
    ([("::", 8787)], set()),
    ([("192.168.1.5", 8787)], set()),  # another interface only
])
def test_a_relay_records_only_the_loopbacks_it_bound(monkeypatch, tmp_path,
                                                     bound, expected) -> None:
    from quorus import relay as relay_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    relay_mod.record_own_relay(bound)
    assert own_relays.own_relays(own_relays.own_relays_path()) == expected
