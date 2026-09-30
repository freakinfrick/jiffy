"""`{{token}}` detection, cursor parking and cycling."""
from __future__ import annotations

from jiffy.tui.assemble import assemble
from jiffy.tui.placeholders import first_span, has_placeholder, next_span, spans

LINE = "find {{path}} -name '{{pattern}}'  # find files by name"


def test_detection_and_order():
    assert has_placeholder(LINE)
    assert [LINE[s:e] for s, e in spans(LINE)] == ["{{path}}", "{{pattern}}"]
    assert not has_placeholder("find . -size +100M  # big files")


def test_assembled_carries_the_tokens_in_order():
    a = assemble("find {{path}} -name '{{pattern}}'", "find files by name")
    assert a.placeholders == ["path", "pattern"]
    assert has_placeholder(a.comment_line)


def test_first_span_is_where_the_cursor_parks():
    start, end = first_span(LINE)
    assert LINE[start:end] == "{{path}}"
    assert start == LINE.index("{{path}}")


def test_next_span_cycles_and_wraps():
    first = first_span(LINE)
    second = next_span(LINE, first[0])
    assert LINE[second[0]:second[1]] == "{{pattern}}"
    assert next_span(LINE, second[0]) == first          # wraps


def test_no_tokens_means_no_span():
    assert first_span("ls -la") is None
    assert next_span("ls -la", 0) is None
