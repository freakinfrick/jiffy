"""Colour in the editable line: exact while the line is untouched, honest after."""
from __future__ import annotations

from prompt_toolkit.document import Document

from jiffy.tui.lexer import PlaceholderLexer, spans_for
from jiffy.types import Assembled, Fill

LINE = "find ~ -name '{{*.ext}}'"
FILLS = (Fill("path/to/directory", "~", "query", "", 5, 6),
         Fill("*.ext", "", "", "", 13, 24))
ASSEMBLED = Assembled(LINE, "find logs", f"{LINE}  # find logs", ["*.ext"], FILLS)


def test_an_untouched_line_keeps_the_full_account():
    assert spans_for(LINE, ASSEMBLED) == FILLS


def test_an_edited_line_falls_back_to_what_the_text_still_proves():
    """Offsets stop being true the moment you type; `{{...}}` is still visible."""
    edited = "find /var/log -name '{{*.ext}}'"
    spans = spans_for(edited, ASSEMBLED)
    assert [f.token for f in spans] == ["*.ext"]
    assert all(f.source == "" for f in spans)          # no claim about where a value came from
    assert edited[spans[0].start:spans[0].end] == "{{*.ext}}"


def test_no_answer_at_all_still_marks_tokens():
    spans = spans_for("cp {{src}} {{dst}}", None)
    assert [f.token for f in spans] == ["src", "dst"]


def test_the_lexer_styles_the_buffer_through_the_same_renderer():
    lexer = PlaceholderLexer(lambda: ASSEMBLED)
    frags = lexer.lex_document(Document(LINE, len(LINE)))(0)
    assert "".join(t for _s, t in frags) == LINE
    assert any(s == "class:jiffy.ph.unresolved" for s, _t in frags)


def test_an_empty_line_lexes_to_nothing():
    lexer = PlaceholderLexer(lambda: ASSEMBLED)
    assert lexer.lex_document(Document("", 0))(0) == []


def test_a_broken_answer_source_does_not_break_the_prompt():
    """Typing must never depend on the engine being in a good mood."""
    def boom():
        raise RuntimeError("engine is mid-swap")

    frags = PlaceholderLexer(boom).lex_document(Document(LINE, len(LINE)))(0)
    assert "".join(t for _s, t in frags) == LINE
