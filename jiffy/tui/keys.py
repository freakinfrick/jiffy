"""The key contract, as a registry.

One table, built once, so "what does TAB do" is a fact a test can read rather
than a claim in a docstring. `ui` is any object with the five handlers below —
`Tui` implements them; a test can pass a recorder.

    TAB      accept the autofill into the buffer; DOES NOT run. With
             {{placeholders}} in the buffer, walk to the next one instead.
    ENTER    search the line; once an answer is ready, run it, subject to the gate.
    UP/DOWN  move the selection through the candidate list, always. The list is
             the thing on screen next to the arrows, so the arrows drive it.
    <any>    ordinary self-insert, EXCEPT on a `{{token}}`, where the keystroke
             replaces the whole token: typing is how you fill one.
    ESC      clear back to empty.

History is not lost, it moves: ^P/^N walk it and ^R searches it, both from
prompt_toolkit's own emacs bindings, which this registry no longer shadows.
"""
from __future__ import annotations

from typing import Callable

from prompt_toolkit.application import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

__all__ = ["bindings", "key_names", "CONTRACT"]

#: canonical name -> (prompt_toolkit key, handler attribute, when)
CONTRACT: tuple[tuple[str, str, str, str], ...] = (
    ("tab",     "c-i",    "on_tab",     "always"),
    ("enter",   "c-m",    "on_enter",   "always"),
    ("escape",  "escape", "on_escape",  "always"),
    ("any",     "<any>",  "on_char",    "on-placeholder"),
    ("up",      "up",     "on_prev",    "always"),
    ("down",    "down",   "on_next",    "always"),
)

_ALIASES = {
    Keys.ControlI: "tab", Keys.Tab: "tab",
    Keys.ControlM: "enter", Keys.Enter: "enter",
    Keys.Escape: "escape", Keys.Up: "up", Keys.Down: "down",
    Keys.Any: "any",
}


def _canonical(key) -> str:
    if key in _ALIASES:
        return _ALIASES[key]
    return str(getattr(key, "value", key))


def key_names(kb: KeyBindings) -> set[str]:
    """Every key the registry binds, in this module's canonical spelling."""
    return {_canonical(k) for b in kb.bindings for k in b.keys}


def _on_placeholder() -> bool:
    """Is the cursor sitting on a `{{token}}` right now?"""
    from .placeholders import span_at
    buf = get_app().current_buffer
    return span_at(buf.text, buf.cursor_position) is not None


def bindings(ui, empty: Callable[[], bool] | None = None,
             on_placeholder: Callable[[], bool] | None = None) -> KeyBindings:
    """Build the registry against `ui`. No handler here runs anything itself.

    `empty` backs the `buffer-empty` condition. No row uses it today — the
    arrows are unconditional — but the filter stays wired so a key can be made
    conditional by editing one cell of CONTRACT.
    """
    kb = KeyBindings()
    if empty is None:
        def empty() -> bool:
            return not get_app().current_buffer.text.strip()
    buffer_empty = Condition(empty)
    on_token = Condition(on_placeholder or _on_placeholder)

    for name, key, attr, when in CONTRACT:
        handler = getattr(ui, attr)
        filt = {"buffer-empty": buffer_empty, "on-placeholder": on_token}.get(when)
        deco = kb.add(key, filter=filt) if filt is not None else kb.add(key)
        deco(_wrap(handler))
    return kb


def _wrap(handler):
    def run(event):
        handler(event)
    run.__name__ = getattr(handler, "__name__", "handler")
    return run
