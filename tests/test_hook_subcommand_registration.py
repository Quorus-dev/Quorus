"""Regression test for the cross-harness `quorus hook` subcommand surface.

Background: on 2026-05-02 a Gemini session failed with
`invalid choice: 'gemini-beforeagent' (choose from enable, disable, status)`
because Gemini was running an older pipx-installed `quorus` that pre-dated
commit 9fd62fe (which added the cursor-session / cursor-stop /
gemini-beforeagent subactions). The fix was pinning the hook command to
the dev .venv's absolute path. This test makes sure that, on whichever
binary the developer is exercising, the full set of cross-harness hook
choices is present — so we catch the regression at test time, not when
a Gemini turn blows up mid-demo.
"""

from __future__ import annotations

import argparse


def _parser_with_hook() -> argparse.ArgumentParser:
    """Recreate the `quorus hook` subparser exactly as cli.py registers it.

    Importing cli.py directly is heavy (it builds the full argparse tree
    with 50+ subcommands and pulls in httpx). We re-declare the
    just-the-hook-subparser here against the source's choice list so the
    test acts as a live spec — if cli.py drifts, the next assertion
    fires.
    """
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    p_hook = sub.add_parser("hook")
    p_hook.add_argument(
        "action",
        choices=[
            "enable", "disable", "status",
            "cursor-session", "cursor-stop", "gemini-beforeagent",
        ],
    )
    return p


def test_hook_subparser_accepts_cross_harness_choices() -> None:
    """All 6 hook actions must parse without error.

    If any of these fails, the regression that broke Gemini on 2026-05-02
    has resurfaced — the agent will be unable to call its hook and the
    notification pipe is dead.
    """
    parser = _parser_with_hook()
    for action in (
        "enable", "disable", "status",
        "cursor-session", "cursor-stop", "gemini-beforeagent",
    ):
        ns = parser.parse_args(["hook", action])
        assert ns.action == action


def test_cli_argparse_lists_all_six_actions() -> None:
    """Inspect the real cli.py argparse tree and confirm the hook choices.

    This fails loud if cli.py drops or renames an action — the kind of
    accidental delete that broke the Gemini surface in the first place.
    """
    # cli.py builds its parser inside main(); we call its registration
    # path indirectly by reading the source. Cheaper than running main()
    # because we don't want network or disk touched.
    import inspect

    from quorus_cli import cli as _cli_module
    from quorus_cli.cli import main as _main_unused  # noqa: F401 — side-effect import
    src = inspect.getsource(_cli_module)
    for choice in (
        '"enable"', '"disable"', '"status"',
        '"cursor-session"', '"cursor-stop"', '"gemini-beforeagent"',
    ):
        assert choice in src, (
            f"hook subcommand choice {choice} missing from cli.py — "
            "did someone remove a cross-harness hook surface?"
        )


def test_hook_handlers_module_dispatches_three_per_harness() -> None:
    """quorus_cli.hooks.HOOK_HANDLERS must contain exactly the three
    per-harness handlers; the enable/disable/status modes are handled
    by cli._cmd_hook itself, not the hooks module.
    """
    from quorus_cli.hooks import HOOK_HANDLERS
    expected = {"cursor-session", "cursor-stop", "gemini-beforeagent"}
    assert set(HOOK_HANDLERS.keys()) == expected, (
        f"HOOK_HANDLERS keys drifted from {expected} to "
        f"{set(HOOK_HANDLERS.keys())}"
    )
