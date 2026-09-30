"""The key contract as a registry: what is bound, and what each key does."""
from __future__ import annotations

from prompt_toolkit.buffer import Buffer

from jiffy.tui import keys

REQUIRED = {"tab", "enter", "up", "down", "escape", "any"}


class Recorder:
    """A `ui` that only writes down which handler fired."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _mk(name):                                    # noqa: N805
        def handler(self, event):
            self.calls.append(name)
        return handler

    on_tab = _mk("on_tab")
    on_enter = _mk("on_enter")
    on_next = _mk("on_next")
    on_prev = _mk("on_prev")
    on_escape = _mk("on_escape")
    on_char = _mk("on_char")


def test_registry_binds_the_contract_keys():
    kb = keys.bindings(Recorder(), empty=lambda: True, on_placeholder=lambda: True)
    assert REQUIRED <= keys.key_names(kb)


def test_up_down_cycle_candidates_whatever_the_buffer_holds():
    """The list is on screen while you type, so the arrows drive it unconditionally."""
    names = {name: when for name, _key, _attr, when in keys.CONTRACT}
    assert names["up"] == names["down"] == "always"
    assert names["tab"] == names["enter"] == "always"

    kb = keys.bindings(Recorder(), empty=lambda: False, on_placeholder=lambda: False)
    up = [b for b in kb.bindings if keys._canonical(b.keys[0]) == "up"][0]
    assert up.filter()


def test_history_keys_are_left_to_prompt_toolkit():
    """^N/^P/^R are no longer shadowed: history moved there when the arrows left."""
    bound = keys.key_names(keys.bindings(Recorder(), empty=lambda: True,
                                         on_placeholder=lambda: False))
    assert {"c-n", "c-p", "c-r"} & bound == set()


def test_typing_is_only_intercepted_on_a_placeholder():
    """Everywhere else `<any>` must stay unbound, or jiffy owns every keystroke."""
    off = keys.bindings(Recorder(), empty=lambda: True, on_placeholder=lambda: False)
    on = keys.bindings(Recorder(), empty=lambda: True, on_placeholder=lambda: True)
    def pick(kb):
        return [b for b in kb.bindings if keys._canonical(b.keys[0]) == "any"][0]

    assert not pick(off).filter()
    assert pick(on).filter()


def test_each_key_reaches_its_handler():
    ui = Recorder()
    kb = keys.bindings(ui, empty=lambda: True, on_placeholder=lambda: True)
    fired = {}
    for name, _key, attr, _when in keys.CONTRACT:
        binding = [b for b in kb.bindings if keys._canonical(b.keys[0]) == name][0]
        ui.calls.clear()
        binding.handler(_Event())
        fired[name] = ui.calls[0]
    assert fired["tab"] == "on_tab"
    assert fired["enter"] == "on_enter"
    assert fired["down"] == "on_next"
    assert fired["up"] == "on_prev"
    assert fired["escape"] == "on_escape"
    assert fired["any"] == "on_char"


class _Event:
    current_buffer = Buffer()
