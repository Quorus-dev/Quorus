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
# Truecolor hex values — truncated to rich named colors where needed.
PRIMARY = "#14b8a6"
PRIMARY_DEEP = "#0d9488"
PRIMARY_LIGHT = "#2dd4bf"
PRIMARY_MINT = "#5eead4"
MUTED = "#64748b"
DIM = "#475569"
SUCCESS = "#10b981"
WARNING = "#f59e0b"
ERROR = "#ef4444"
AGENT = "#a78bfa"
ROOM = "#fbbf24"

THEME = Theme(
    {
        "primary": PRIMARY,
        "accent": PRIMARY_MINT,
        "muted": MUTED,
        "dim": DIM,
        "success": SUCCESS,
        "warning": WARNING,
        "error": ERROR,
        "agent": AGENT,
        "room": ROOM,
        "prompt": f"bold {PRIMARY}",
        "heading": f"bold {PRIMARY}",
    }
)

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
_BANNER_LINES = [
    " ██████╗ ██╗   ██╗ ██████╗ ██████╗ ██╗   ██╗███████╗",
    "██╔═══██╗██║   ██║██╔═══██╗██╔══██╗██║   ██║██╔════╝",
    "██║   ██║██║   ██║██║   ██║██████╔╝██║   ██║███████╗",
    "██║▄▄ ██║██║   ██║██║   ██║██╔══██╗██║   ██║╚════██║",
    "╚██████╔╝╚██████╔╝╚██████╔╝██║  ██║╚██████╔╝███████║",
    " ╚══▀▀═╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝",
]

_TINY_BANNER = "quorus"

_GRADIENT = [PRIMARY_DEEP, PRIMARY, PRIMARY_LIGHT, PRIMARY_MINT]


def _gradient_text(line: str) -> Text:
    """Apply horizontal gradient across each non-space char."""
    text = Text()
    n = len(_GRADIENT)
    printable_chars = [c for c in line if c != " "]
    total = max(1, len(printable_chars))
    idx = 0
    for ch in line:
        if ch == " ":
            text.append(ch)
            continue
        color = _GRADIENT[min(n - 1, idx * n // total)]
        text.append(ch, style=color)
        idx += 1
    return text


def banner(version: str = "0.4.0", tagline: bool = True) -> None:
    """Print the Quorus banner with teal gradient."""
    width = console.size.width
    if width < 60:
        console.print(f"[bold primary]{_TINY_BANNER}[/] [muted]v{version}[/]")
        if tagline:
            console.print("[muted]coordination for agent swarms[/]")
        console.print()
        return
    console.print()
    for line in _BANNER_LINES:
        console.print(_gradient_text(line))
    if tagline:
        console.print(
            f"[muted]          coordination for agent swarms · v{version}[/]"
        )
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
    console.rule(f"[heading]{title}[/]", style="primary", align="left")


def footer(hint: str) -> None:
    console.print(f"\n[dim]{hint}[/]")


def hint_next_steps(steps: list[str]) -> None:
    """Render a 'Next steps' panel after a successful setup command."""
    body = "\n".join(f"[primary]{ICON_INFO}[/] {s}" for s in steps)
    console.print(
        Panel(
            body,
            title="[heading]Next steps[/]",
            border_style="primary",
            padding=(1, 2),
            title_align="left",
        )
    )


# ── Spinner ──────────────────────────────────────────────────────────────────
@contextmanager
def spinner(text: str) -> Iterator[None]:
    """Braille spinner in teal while a block of work runs."""
    with console.status(f"[primary]{text}[/]", spinner="dots", spinner_style="primary"):
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
    body.append(snippet, style=PRIMARY_MINT)
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
    body.append(f"  {token}\n", style=PRIMARY_MINT)
    if expires_in:
        body.append(f"\n  expires in {expires_in}", style=MUTED)
    console.print(
        Panel(
            body,
            border_style="primary",
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
