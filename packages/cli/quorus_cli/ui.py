"""Shared UI primitives for the Quorus CLI and TUI.

Centralizes the theme, banner, icons, and helper renderers so every
command renders with the same palette and tone. Import `console` from
here instead of creating local Console instances.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

# ── Palette ──────────────────────────────────────────────────────────────────
# tui-polish.md §3.2. Words never carry a hex colour: text is the terminal's
# default foreground (bold or dim for emphasis), so it reads on dark and light
# themes alike. 4.5:1 against both #1e1e1e and #ffffff is impossible for any
# colour (the best grey manages 4.06:1). Hue lives only in MARKS below: glyphs,
# dots, rules and borders, each ≥ 3:1 on both (tests/test_tui_contrast.py).
PRIMARY = "#0d9488"            # brand teal — rules, borders, the prompt bar
PRIMARY_MINT = "#0891b2"       # info marks
SUCCESS = "#059669"
WARNING = "#d97706"
ERROR = "#ef4444"

# Sender palette — 6 hues for the dot beside each member's name, hashed
# deterministically. Same families as before (no red/green adjacency), at
# mid-tones so a dot reads on light terminals too.
SENDER_TEAL    = "#0d9488"
SENDER_INDIGO  = "#6366f1"
SENDER_AMBER   = "#d97706"
SENDER_ROSE    = "#f43f5e"
SENDER_VIOLET  = "#8b5cf6"
SENDER_CYAN    = "#0284c7"

STYLES = {
    # Text: the terminal's default foreground, with attributes only. These
    # carry words people read (names, rooms, commands, headings, messages).
    "primary": "bold",
    "accent": "bold",
    "muted": "dim",
    "dim": "dim",
    "subtle": "none",
    "bright": "none",
    "agent": "bold",
    "room": "bold",
    "prompt": "bold",
    "heading": "bold",
    "pill": "bold",
    "pill_idle": "dim",
    "kbd": "bold",
    "ts": "dim",
}
# Marks: hue for glyphs, dots, rules and borders only (✓ ✗ ! ● │ ─), never for
# words. Non-text contrast (WCAG 1.4.11) asks 3:1, met on dark and light.
MARKS = {
    "brand": PRIMARY,
    "success": SUCCESS,
    "warning": WARNING,
    "error": ERROR,
    "danger": ERROR,
    "info": PRIMARY_MINT,
    # Sender dots — hashed deterministically by chat.py so the same name
    # always gets the same dot; the name itself stays default foreground.
    "sender1": SENDER_TEAL,
    "sender2": SENDER_INDIGO,
    "sender3": SENDER_AMBER,
    "sender4": SENDER_ROSE,
    "sender5": SENDER_VIOLET,
    "sender6": SENDER_CYAN,
}
THEME = Theme({**STYLES, **MARKS})

def _no_color() -> bool:
    """Honor the NO_COLOR spec (https://no-color.org/)."""
    import os

    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"


console = Console(theme=THEME, highlight=False, no_color=_no_color())

# ── Icons ────────────────────────────────────────────────────────────────────
ICON_OK = "✓"
ICON_FAIL = "✗"
ICON_INFO = "→"
ICON_WARN = "!"
ICON_PROMPT = "❯"
ICON_BULLET = "·"
ICON_DOT_LIVE = "⏵"

# ── Banner ───────────────────────────────────────────────────────────────────
TAGLINE = "one group chat for you and your agents"


def wordmark(version: str, tagline: bool = True) -> Text:
    """``quorus 0.7.4 — one group chat for you and your agents``, one line at any
    width (tui-polish.md §3.4: the 6-line block letters ate the first screen)."""
    line = Text("quorus", style="bold primary")
    line.append(f" {version}", style="muted")
    if tagline:
        line.append(f" — {TAGLINE}", style="muted")
    return line


def banner(version: str | None = None, tagline: bool = True) -> None:
    """Print the one-line wordmark. *version* defaults to the installed
    package's (it said v0.5.0 on 0.5.2: new-user pass 1, F2)."""
    if version is None:
        from quorus import __version__ as version
    console.print()
    console.print(wordmark(version, tagline))
    console.print()


# ── Status primitives ────────────────────────────────────────────────────────
def success(msg: str, hint: str | None = None) -> None:
    console.print(f"[success]{ICON_OK}[/] {msg}")
    if hint:
        console.print(f"  [muted]{hint}[/]")


def error(msg: str, hint: str | None = None) -> None:
    console.print(f"[error]{ICON_FAIL}[/] {msg}")
    if hint:
        console.print(f"  [muted]hint: {hint}[/]")


def info(msg: str) -> None:
    console.print(f"[primary]{ICON_INFO}[/] {msg}")


def warn(msg: str, hint: str | None = None) -> None:
    console.print(f"[warning]{ICON_WARN}[/] {msg}")
    if hint:
        console.print(f"  [muted]{hint}[/]")


def heading(title: str) -> None:
    console.print()
    console.rule(f"[heading]{title}[/]", style="brand", align="left")


def footer(hint: str) -> None:
    console.print(f"\n[dim]{hint}[/]")


def hint_next_steps(steps: list[str]) -> None:
    """Render a 'Next steps' panel after a successful setup command."""
    body = "\n".join(f"[primary]{ICON_INFO}[/] {s}" for s in steps)
    console.print(
        Panel(
            body,
            title="[heading]Next steps[/]",
            border_style="brand",
            padding=(1, 2),
            title_align="left",
        )
    )


# ── Spinner ──────────────────────────────────────────────────────────────────
@contextmanager
def spinner(text: str) -> Iterator[None]:
    """Braille spinner in teal while a block of work runs."""
    with console.status(f"[primary]{text}[/]", spinner="dots", spinner_style="brand"):
        yield


# ── Prompt ───────────────────────────────────────────────────────────────────
def prompt_symbol() -> str:
    return f"[prompt]{ICON_PROMPT}[/]"


# ── Formatters ───────────────────────────────────────────────────────────────
def fmt_room(name: str) -> str:
    """#room-name in amber."""
    clean = name.lstrip("#")
    return f"[room]#{clean}[/]"


def fmt_agent(name: str) -> str:
    """@agent-name in purple."""
    clean = name.lstrip("@")
    return f"[agent]@{clean}[/]"


def fmt_status_bar(
    room: str, agent_count: int, relay_host: str, streaming: bool = True
) -> str:
    """Render the status bar as a styled string (consumer decides placement)."""
    if streaming:
        live = f"[success]{ICON_DOT_LIVE}[/] streaming"
    else:
        live = f"[error]{ICON_DOT_LIVE}[/] offline"
    sep = f" [dim]{ICON_BULLET}[/] "
    return (
        f" {fmt_room(room)}{sep}[muted]{agent_count} agents online[/]"
        f"{sep}[muted]{relay_host}[/]{sep}{live}"
    )


# ── Extended primitives ──────────────────────────────────────────────────────
# New helpers used across CLI commands so nothing has to build panels or
# raw markup ad-hoc. Add new call sites here instead of at the call sites.


def code(snippet: str, *, lang: str = "bash") -> None:
    """Indented, tinted command block — use for copyable shell lines.

    Renders as a single line: `  $ <command>` with the prompt in primary
    and the command in mint. `lang` is accepted for future syntax
    highlighting; currently shell-style only.
    """
    del lang
    body = Text()
    body.append("  ")
    body.append("$ ", style=f"bold {PRIMARY}")
    body.append(snippet, style="bold")
    console.print(body)


def steps(items: list[str], *, title: str | None = None) -> None:
    """Numbered, indented steps in a muted panel.

    Unlike `hint_next_steps` which uses arrow bullets, this primitive
    renders an ordered list (1. 2. 3.) for step-by-step instructions.
    """
    lines = "\n".join(f"[primary]{i}.[/] {s}" for i, s in enumerate(items, 1))
    console.print(
        Panel(
            lines,
            title=f"[heading]{title}[/]" if title else None,
            border_style="dim",
            padding=(1, 2),
            title_align="left",
        )
    )


def token_box(token: str, *, label: str = "Token", expires_in: str | None = None) -> None:
    """Prominent token display — teal border, token in accent.

    Use for share/invite output so the token is visually distinct from
    plain code blocks. Pass `expires_in` as a human string
    (e.g. "7 days") — rendered below the token in muted text.
    """
    body = Text()
    body.append(f"  {label}\n", style=f"bold {PRIMARY}")
    body.append(f"  {token}\n", style="bold")
    if expires_in:
        body.append(f"\n  expires in {expires_in}", style="dim")
    console.print(
        Panel(
            body,
            border_style="brand",
            padding=(1, 1),
        )
    )


def error_with_retry(msg: str, relay_url: str | None = None) -> None:
    """Error + contextual hint. Pings relay to pick 'relay down' vs 'retry'.

    When a relay_url is provided, this does a short /health probe to
    decide whether the underlying cause is likely network or a real
    relay-side error, and picks the hint accordingly.
    """
    console.print(f"[error]{ICON_FAIL}[/] {msg}")
    if not relay_url:
        return
    try:
        import httpx

        r = httpx.get(f"{relay_url.rstrip('/')}/health", timeout=2)
        if r.status_code == 200:
            console.print(
                f"  [muted]hint: retry — relay is up at {relay_url}[/]"
            )
            return
    except Exception:
        pass
    console.print(
        f"  [muted]hint: relay at {relay_url} is unreachable[/]"
    )
    console.print(
        "  [muted]      start it with[/] [accent]quorus relay[/]"
        "[muted], or run[/] [accent]quorus doctor[/]"
    )
