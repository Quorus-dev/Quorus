"""`quorus --help` must list the commands the README and site tell people to run.

The 0.6.3 audit found `web`, `invites` and `release-name` documented but missing from
the grouped help, so a stranger reading `quorus --help` couldn't find them."""

import re
from pathlib import Path

from quorus_cli import cli

ROOT = Path(__file__).resolve().parents[1]


def _help_text(capsys) -> str:
    cli._print_grouped_help()
    return capsys.readouterr().out


def test_documented_commands_are_in_the_grouped_help(capsys) -> None:
    text = _help_text(capsys)
    listed = set(re.findall(r"^  ([a-z][a-z0-9-]*)", text, flags=re.M))
    docs = (ROOT / "README.md").read_text()
    docs += "".join(p.read_text() for p in (ROOT / "website").glob("*.html"))
    real = set(re.findall(r'sub\.add_parser\(\s*"([a-z][a-z0-9-]*)"',
                          (ROOT / "packages/cli/quorus_cli/cli.py").read_text()))
    documented = {c for c in re.findall(r"\bquorus ([a-z][a-z0-9-]+)", docs) if c in real}
    missing = sorted(documented - listed)
    assert not missing, f"documented but not in `quorus --help`: {missing}"
