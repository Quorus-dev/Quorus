"""Claude-Code-style autocomplete popover for `/` slash commands and `@`
mentions in the Quorus TUI composer.

Pure-render + keystroke logic. No I/O, no termios, no relay calls. The hub
owns the input loop; this module owns popover state and rendering.

State machine:
    HIDDEN ── user types `/` at col 0 ──> OPEN_SLASH
    HIDDEN ── user types `@` after word boundary ──> OPEN_MENTION
    OPEN_*  ── prefix grows / shrinks ──> filter() re-runs, popover redraws
    OPEN_*  ── Tab/Enter ──> ACCEPTING (caller commits the replacement)
    OPEN_*  ── Esc / space-after-word / non-matching char ──> HIDDEN

Render contract:
    * Returns ``list[Text]`` rows. Caller prints them above the prompt.
    * Max 8 visible items (scroll window slides as selected_idx changes).
    * Selected row uses the ``accent`` background + bold for visibility.
    * Items are ``(label, description)`` tuples. Label is e.g. ``/join`` or
      ``@arav``; description is a short one-liner shown in muted text.

Keystroke contract:
    * ``handle_key(key)`` returns ``"accept" | "dismiss" | "next" | "prev"
      | "consume" | None``. None means "not handled — pass through".
    * ``"consume"`` means the popover swallowed the key (e.g., the user
      typed a letter that grew the prefix); caller does NOT echo it
      separately — the popover already updated its own state and the
      caller should re-render.

The caller is responsible for: appending typed chars to the input buffer,
intercepting Tab/Enter when the popover is open, replacing the typed
prefix with the accepted label, and dismissing on space/word-boundary.
"""

from __future__ import annotations

from typing import Callable, Literal, Optional

from rich.text import Text

# Visible window — at most this many rows render; the selected row stays
# inside the window via a sliding cursor.
_MAX_VISIBLE = 8
# Hard width cap. Caller may pass a smaller cap; this is the upper bound.
_MAX_WIDTH = 60

# State kinds. HIDDEN means the popover is dormant; the two OPEN_* variants
# distinguish which item-source to filter against.
PopoverKind = Literal["hidden", "slash", "mention"]
PopoverAction = Literal["accept", "dismiss", "next", "prev", "consume"]


class AutocompletePopover:
    """Owns the popover's UI state.

    Intentionally not a frozen dataclass — the hub mutates ``prefix``,
    ``items``, and ``selected_idx`` in place during a typing session, and
    a class with explicit transition methods reads better than a
    dataclasses.replace() chain at every keystroke.
    """

    def __init__(
        self,
        *,
        slash_items_provider: Callable[[], list[tuple[str, str]]] | None = None,
        mention_items_provider: Callable[[], list[tuple[str, str]]] | None = None,
    ) -> None:
        self.kind: PopoverKind = "hidden"
        self.prefix: str = ""
        # Cached source items — refreshed whenever the popover opens.
        # Each tuple is (label, description). Label includes the `/` or `@`
        # prefix so callers can splice it directly into the input buffer.
        self._source: list[tuple[str, str]] = []
        self.items: list[tuple[str, str]] = []
        self.selected_idx: int = 0
        # Sliding window into ``items`` so selected stays visible.
        self._window_top: int = 0
        # Pluggable source providers — kept on the instance so the hub
        # passes them once at construction and we don't have to thread
        # them through every open() call. Both default to empty.
        self._slash_provider = slash_items_provider or (lambda: [])
        self._mention_provider = mention_items_provider or (lambda: [])

    # ── Open / close transitions ─────────────────────────────────────────

    def open_slash(self) -> None:
        """Transition HIDDEN → OPEN_SLASH. Loads slash items, prefix=`/`."""
        self.kind = "slash"
        self.prefix = "/"
        self._source = list(self._slash_provider())
        self._refilter()

    def open_mention(self) -> None:
        """Transition HIDDEN → OPEN_MENTION. Loads members, prefix=`@`."""
        self.kind = "mention"
        self.prefix = "@"
        self._source = list(self._mention_provider())
        self._refilter()

    def dismiss(self) -> None:
        """Reset to HIDDEN. Idempotent."""
        self.kind = "hidden"
        self.prefix = ""
        self._source = []
        self.items = []
        self.selected_idx = 0
        self._window_top = 0

    @property
    def is_open(self) -> bool:
        return self.kind != "hidden"

    # ── Prefix / item maintenance ────────────────────────────────────────

    def append_char(self, ch: str) -> None:
        """Grow the prefix by one char and re-filter.

        Caller has already decided the char is a continuation (printable,
        not a space, not a word boundary). We don't validate here — keeps
        this hot path branch-free.
        """
        if not self.is_open:
            return
        self.prefix += ch
        self._refilter()

    def backspace(self) -> bool:
        """Shrink the prefix. Returns True if we dismissed (prefix went
        empty past the leading sigil — i.e., the user backed up over the
        original `/` or `@`).
        """
        if not self.is_open:
            return False
        if len(self.prefix) <= 1:
            self.dismiss()
            return True
        self.prefix = self.prefix[:-1]
        self._refilter()
        return False

    def _refilter(self) -> None:
        """Rank items: prefix-match-first, substring-match-second.

        Match is on the part of the label after the leading sigil
        (so ``/jo`` matches ``/join`` cleanly). Case-insensitive. Stable
        ordering inside each tier preserves the source's original order
        — slash commands have a deliberate sequence in the registry.
        """
        q = self.prefix[1:].lower()  # drop leading `/` or `@`
        if not q:
            # Bare sigil — show everything in source order, no ranking.
            self.items = list(self._source)
        else:
            prefix_hits: list[tuple[str, str]] = []
            substring_hits: list[tuple[str, str]] = []
            seen_labels: set[str] = set()
            for label, desc in self._source:
                stem = label.lstrip("/@").lower()
                if stem.startswith(q):
                    prefix_hits.append((label, desc))
                    seen_labels.add(label)
                elif q in stem and label not in seen_labels:
                    substring_hits.append((label, desc))
            self.items = prefix_hits + substring_hits
        # Reset selection to first item; clamp window.
        self.selected_idx = 0
        self._window_top = 0

    # ── Keystroke handling ───────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[PopoverAction]:
        """Translate a key into a popover action.

        Caller drives this: it intercepts the key while ``is_open`` and
        either applies the returned action or echoes the key to its own
        buffer. We never mutate the caller's state — only ours.

        Returns:
            "next" / "prev"  — selected_idx already moved
            "accept"         — caller should splice items[selected_idx]
                               into its buffer
            "dismiss"        — popover already reset to HIDDEN
            "consume"        — char appended to prefix; caller re-renders
            None             — pass-through, popover did not handle it
        """
        if not self.is_open:
            return None
        # Non-printable / sentinel keys come in as multi-char strings
        # ("UP", "DOWN", "ENTER", "TAB", "ESC", "BACKSPACE") so the caller
        # can be readchar-agnostic. Single-char strings are treated as
        # printable input.
        k = key.upper() if len(key) > 1 else key
        if k in ("UP", "PREV"):
            if self.items:
                self.selected_idx = max(0, self.selected_idx - 1)
                self._scroll_into_view()
            return "prev"
        if k in ("DOWN", "NEXT"):
            if self.items:
                self.selected_idx = min(len(self.items) - 1, self.selected_idx + 1)
                self._scroll_into_view()
            return "next"
        if k in ("TAB", "ENTER"):
            if not self.items:
                self.dismiss()
                return "dismiss"
            return "accept"
        if k == "ESC":
            self.dismiss()
            return "dismiss"
        if k == "BACKSPACE":
            self.backspace()
            return "consume"
        # Single printable char.
        if len(key) == 1 and key.isprintable():
            # Space dismisses — `/help ` or `@arav ` ends the autocomplete
            # context.
            if key == " ":
                self.dismiss()
                return "dismiss"
            self.append_char(key)
            return "consume"
        # Unknown sentinel — let the caller decide.
        return None

    def _scroll_into_view(self) -> None:
        """Slide the visible window so selected_idx is inside it."""
        if self.selected_idx < self._window_top:
            self._window_top = self.selected_idx
        elif self.selected_idx >= self._window_top + _MAX_VISIBLE:
            self._window_top = self.selected_idx - _MAX_VISIBLE + 1

    # ── Selection accessors ──────────────────────────────────────────────

    def selected_label(self) -> str | None:
        """Return the currently-selected label, or None if no items."""
        if not self.items:
            return None
        return self.items[self.selected_idx][0]

    # ── Render ───────────────────────────────────────────────────────────

    def render(self, console_width: int) -> list[Text]:
        """Return the popover as a list of Rich Text rows.

        Empty list when not open (or no items match) — caller skips the
        print. Width caps at min(60, console_width - 4) so we never run
        off-screen and always leave room for the leading two-space
        indent that matches the rest of the chat surface.
        """
        if not self.is_open:
            return []
        width = max(20, min(_MAX_WIDTH, console_width - 4))
        if not self.items:
            # Render an explicit "no matches" hint instead of vanishing —
            # quieter than popping the popover open and shut.
            row = Text("  ")
            row.append("(no matches)", style="dim italic")
            return [row]

        rows: list[Text] = []
        # Header — small caption so the popover is identifiable.
        head = Text("  ")
        if self.kind == "slash":
            head.append("/", style="kbd")
            head.append("  Slash command  ", style="dim")
        else:
            head.append("@", style="kbd")
            head.append("  Mention  ", style="dim")
        head.append("Tab", style="kbd")
        head.append(" to insert  ", style="dim")
        head.append("Esc", style="kbd")
        head.append(" to cancel", style="dim")
        rows.append(head)

        # Window slice — at most _MAX_VISIBLE rows.
        top = self._window_top
        slice_ = self.items[top : top + _MAX_VISIBLE]
        # Pre-compute label column width so descriptions align.
        label_col = min(20, max((len(lbl) for lbl, _ in slice_), default=1) + 2)
        for offset, (label, desc) in enumerate(slice_):
            idx = top + offset
            is_selected = idx == self.selected_idx
            row = Text("  ")
            if is_selected:
                # Highlighted row — accent bg + bold. Pad to width so the
                # background fills the popover edge-to-edge.
                marker = Text("> ", style="bold accent")
                row.append_text(marker)
                lbl_text = Text(
                    label.ljust(label_col), style="bold accent",
                )
                row.append_text(lbl_text)
                desc_text = Text(_truncate(desc, width - label_col - 4))
                desc_text.stylize("bold accent")
                row.append_text(desc_text)
            else:
                row.append("  ", style="dim")
                # Color the leading sigil so /-vs-@ is identifiable at
                # a glance even with no selection.
                sigil_style = "kbd" if label.startswith(("/", "@")) else "muted"
                row.append(label[0], style=sigil_style)
                row.append(label[1:].ljust(label_col - 1), style="bright")
                row.append(_truncate(desc, width - label_col - 4), style="muted")
            rows.append(row)
        # Footer — only render when there's overflow so the "more" cue
        # isn't noise on small lists.
        if len(self.items) > _MAX_VISIBLE:
            visible_end = top + len(slice_)
            more = len(self.items) - visible_end
            tail = Text("  ")
            if more > 0:
                tail.append(f"+{more} more", style="dim italic")
            else:
                tail.append("end of list", style="dim italic")
            rows.append(tail)
        return rows


# ── Helpers ──────────────────────────────────────────────────────────────


def _truncate(s: str, width: int) -> str:
    """Truncate ``s`` to ``width`` chars, appending ``…`` if cut."""
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width <= 1:
        return "…"
    return s[: width - 1] + "…"


def detect_open_trigger(buf: list[str], typed_char: str) -> PopoverKind:
    """Decide whether ``typed_char`` should open the popover.

    The hub calls this BEFORE appending ``typed_char`` to ``buf``. Two
    triggers match Claude-Code / Slack behavior:
        * `/` at column 0 → open_slash
        * `@` either at column 0 or after a whitespace char → open_mention

    Returns "hidden" when no trigger matched. The hub still appends the
    char regardless — opening the popover doesn't change the buffer.
    """
    if typed_char == "/" and not buf:
        return "slash"
    if typed_char == "@":
        if not buf:
            return "mention"
        # Word boundary = previous char is whitespace. Anything else
        # (letters, digits, punctuation) is mid-word and shouldn't
        # trigger — `email@host.com` must not pop the picker.
        prev = buf[-1]
        if prev.isspace():
            return "mention"
    return "hidden"


def slash_items_from_registry(
    registry: dict[str, tuple[str, object]],
) -> list[tuple[str, str]]:
    """Adapt the existing SLASH_COMMANDS dict shape to popover items.

    Registry maps verb → (description, handler). We strip the handler and
    keep the registration order — the dict is curated (most-common verbs
    first) so the popover preserves that intent.
    """
    return [(verb, desc) for verb, (desc, _h) in registry.items()]


def mention_items_from_room(
    members: list[str],
    *,
    is_human: Callable[[str], bool],
) -> list[tuple[str, str]]:
    """Build mention popover items from a room's member list.

    Each item is ``("@<name>", "(human)" | "(agent)")``. The ``is_human``
    predicate is injected so the popover stays decoupled from chat.py's
    sender-classification heuristics.
    """
    items: list[tuple[str, str]] = []
    for name in members:
        if not name:
            continue
        kind = "(human)" if is_human(name) else "(agent)"
        items.append((f"@{name}", kind))
    return items
