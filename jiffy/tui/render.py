"""What the user sees: ghost text, the gate banner, the footer.

Pure formatting. Every function takes values and returns prompt_toolkit
`FormattedText`; none of them decide anything, so the presentation of a tier can
be asserted in a test without a terminal.
"""
from __future__ import annotations

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

from jiffy.corpus.index import installed
from jiffy.types import Answer, Tier

__all__ = ["STYLE", "ghost", "banner", "candidates", "gloss", "footer", "settle", "fragments",
           "judge_that_answered", "degraded", "tier_tag", "LIST_LIMIT", "RAIL", "MARGIN"]

STYLE = Style.from_dict({
    "bottom-toolbar": "noreverse bg:default fg:ansibrightblack",
    "bottom-toolbar.text": "noreverse",
    "jiffy.prompt": "bold",
    "jiffy.ghost": "fg:ansibrightblack italic",
    "auto-suggestion": "fg:ansibrightblack italic",
    "jiffy.deny": "fg:ansired bold strike",
    "jiffy.deny.reason": "fg:ansired bold",
    "jiffy.confirm": "bg:ansired fg:ansiwhite bold",
    "jiffy.note": "fg:ansiyellow",
    "jiffy.footer": "fg:ansibrightblack",
    "jiffy.pending": "fg:ansicyan",
    "jiffy.error": "fg:ansired",
    "jiffy.cand": "fg:ansibrightblack",
    "jiffy.cand.sel": "bold",
    "jiffy.cand.deny": "fg:ansired strike",
    "jiffy.cand.more": "fg:ansibrightblack italic",
    "jiffy.rail": "fg:#3f3f3f",
    # the flag reads as part of the line it came from; what it does is annotation
    "jiffy.gloss.flag": "",
    "jiffy.gloss": "fg:ansibrightblack",
    # a filled value says where it came from: your words read as text, anything
    # inferred from the box is greyed, and what could not be resolved is the only
    # red on screen. The braces stay in the string but stop being furniture.
    "jiffy.ph.brace": "fg:#3a3a3a",
    "jiffy.ph.unresolved": "fg:ansired",
    "jiffy.ph.inferred": "fg:ansibrightblack",
    "jiffy.ph.default": "fg:ansiblue",
})

SEL_MARK = "\u25b8 "       # the selected row's caret
MARGIN = "  "              # the one left margin in the whole prompt
RAIL = "\u2502"            # dim rail grouping the candidates into one object
ROW_INDENT = MARGIN
LIST_LIMIT = 6             # rows shown at once; the rest collapse to "+N more"
SEP = " \u00b7 "           # footer separator

GHOST_SEP = "   "          # what separates the typed request from the ghost line
GHOST_ARROW = "→ "    # →
PENDING = "… thinking"


def ghost(line: str) -> str:
    """The inline preview appended after the cursor while the user types."""
    return f"{GHOST_SEP}{GHOST_ARROW}{line}" if line else ""


def settle(columns: int) -> str:
    """Escapes that put the cursor at column 0 without ever adding a blank line.

    A command whose output does not end in a newline leaves the cursor mid-row,
    and the next prompt draws straight over that last partial line — `printf`,
    `echo -n` and every progress meter lose their final line this way.

    Asking the terminal where the cursor is (CPR) is not portable, so this uses
    zsh's trick instead: print enough spaces to wrap the row, then return and
    erase. Mid-row, the padding wraps and the erase lands on the fresh row, so a
    newline is what happened. At column 0 the padding stays on the row and the
    erase wipes it, so nothing happened. Either way the cursor ends at column 0
    with no output destroyed and no gap invented.
    """
    return " " * max(1, columns - 1) + "\r\x1b[K"


def tier_tag(tier: Tier) -> str:
    return {Tier.DENY: "DENY", Tier.CONFIRM: "CONFIRM",
            Tier.NOTE: "note", Tier.CLEAN: ""}[tier]


def banner(line: str, v, confirmed: bool = False) -> FormattedText:
    """The gate's presentation of one line. One row, four shapes.

    DENY     the line struck through, then the reason and the rule id.
    CONFIRM  a red banner naming the reason; a second ENTER runs it.
    NOTE     a yellow tag beside the line.
    CLEAN    nothing.
    """
    if v is None or not line:
        return FormattedText([])
    if v.tier >= Tier.DENY:
        return FormattedText([
            ("class:jiffy.deny", line),
            ("", "  "),
            ("class:jiffy.deny.reason", f"DENIED — {v.reason}  [{v.rule_id}]"),
        ])
    if v.tier == Tier.CONFIRM:
        tail = "ENTER again to run, ESC to edit" if confirmed else "ENTER to confirm, ESC to edit"
        return FormattedText([
            ("class:jiffy.confirm", f" CONFIRM — {v.reason}  [{v.rule_id}] "),
            ("", f"  {tail}"),
        ])
    if v.tier == Tier.NOTE:
        return FormattedText([("class:jiffy.note", f"note: {v.reason}  [{v.rule_id}]")])
    return FormattedText([])


def fragments(line: str, fills=(), base: str = "") -> list[tuple[str, str]]:
    """One command line, split so each span carries where its value came from.

    Spans come from `fill`, which recorded exactly where it wrote each value, so
    this never re-guesses by searching the string. Text outside every span — the
    command, its flags — is drawn in `base`.
    """
    spans = sorted((f for f in fills if f.start >= 0 and f.end <= len(line) and f.end > f.start),
                   key=lambda f: f.start)
    out: list[tuple[str, str]] = []
    at = 0
    for f in spans:
        if f.start < at:                      # overlapping spans: trust the first
            continue
        if f.start > at:
            out.append((base, line[at:f.start]))
        text = line[f.start:f.end]
        if not f.value:                       # still a token: dim braces, loud middle
            out.append(("class:jiffy.ph.brace", text[:2]))
            out.append(("class:jiffy.ph.unresolved", text[2:-2]))
            out.append(("class:jiffy.ph.brace", text[-2:]))
        elif f.source == "default":           # a fallback the request never named
            out.append(("class:jiffy.ph.default", text))
        elif f.source == "query":             # the user's own words: nothing to flag
            out.append((base, text))
        else:                                 # inferred from the box, or a spelling
            out.append(("class:jiffy.ph.inferred", text))
        at = f.end
    if at < len(line):
        out.append((base, line[at:]))
    return out or [(base, line)]


def candidates(options, index: int = 0, limit: int = LIST_LIMIT) -> list[FormattedText]:
    """The cycle list as rows, windowed around the selection.

    One row per candidate, the selected one marked and bright. A row the gate
    denies is struck through here too, so a line that ENTER will refuse never
    looks like a line you can pick. Long lists scroll: the window always holds
    the selection, and what is hidden is named rather than dropped silently.
    """
    opts = [o for o in (options or []) if not o.empty]
    if len(opts) < 2:
        return []
    index = max(0, min(index, len(opts) - 1))
    start = max(0, min(index - limit // 2, len(opts) - limit))
    window = opts[start:start + limit]

    rows: list[FormattedText] = []
    for offset, opt in enumerate(window):
        selected = start + offset == index
        mark = SEL_MARK if selected else " " * len(SEL_MARK)
        gutter = [("", MARGIN), ("class:jiffy.rail", RAIL), ("", " "), ("class:jiffy.cand", mark)]
        if opt.verdict is not None and opt.verdict.tier >= Tier.DENY:
            body = [("class:jiffy.cand.deny", opt.assembled.line)]
        else:
            base = "class:jiffy.cand.sel" if selected else "class:jiffy.cand"
            body = fragments(opt.assembled.line, getattr(opt.assembled, "fills", ()), base)
        rows.append(FormattedText([*gutter, *body]))
    hidden = len(opts) - len(window)
    if hidden > 0:
        rows.append(FormattedText([
            ("", MARGIN), ("class:jiffy.rail", RAIL), ("", " "),
            ("class:jiffy.cand.more", f"{' ' * len(SEL_MARK)}+{hidden} more"),
        ]))
    return rows


GLOSS_COL = 16             # flags wider than this push their own description right


def gloss(pairs, columns: int = 80) -> list[FormattedText]:
    """One row per flag: the flag as written, then what its page says it does.

    Clipped to the pane rather than wrapped: a wrapped row would land in the
    flag column and read as another flag.
    """
    if not pairs:
        return []
    col = max((len(f) for f, _ in pairs if len(f) <= GLOSS_COL), default=0)
    rows = []
    for flag, what in pairs:
        room = max(0, columns - len(MARGIN) - max(col, len(flag)) - 3)
        if len(what) > room:
            what = what[:max(0, room - 1)].rstrip() + "\u2026" if room else ""
        rows.append(FormattedText([("", MARGIN), ("class:jiffy.gloss.flag", flag.ljust(col)),
                                   ("", "  "), ("class:jiffy.gloss", what)]))
    return rows


def judge_that_answered(ans: Answer | None) -> str:
    """The backend that actually produced this line, not the one the config names."""
    if ans is None:
        return "-"
    # A degraded stage kept its input, so its judge did NOT produce this line.
    named = [n.judge for n in ans.trace.nodes if n.judge and not n.degraded]
    return named[-1] if named else "none"


def degraded(ans: Answer | None) -> bool:
    return bool(ans and any(n.degraded for n in ans.trace.nodes))


def degraded_note(ans: Answer | None) -> FormattedText:
    """Why the judge did not answer, as a row of its own.

    The footer says `degraded` too, but it is the rprompt, and prompt_toolkit drops
    the rprompt when the typed line fills a narrow pane — so a word-match answer
    (`top` for "the top 2 layers of the tree") looked like a judged one.
    """
    if not degraded(ans):
        return FormattedText([])
    bad = [n for n in ans.trace.nodes if n.degraded]
    if any("BudgetExceeded" in (n.detail or "") for n in bad):
        why = "daily judge budget reached (usd_cap): no new answers until tomorrow"
    else:
        why = f"judge unavailable at {', '.join(n.node_id for n in bad)}: answer is a fallback"
    return FormattedText([("class:jiffy.note", why)])


def not_installed_note(name: str) -> FormattedText:
    """Say so when the line on offer runs a command this box does not have.

    ENTER would only print "command not found"; a row that names the missing
    program is the useful version of that, before it happens.
    """
    head = name.split()[0] if name else ""
    if not head or installed(head):
        return FormattedText([])
    return FormattedText([("class:jiffy.note", f"{head} is not installed here")])


# Measured pipeline p50 (held-out bench, 106 cases). Used only to tell the user how
# long a query is likely to take BEFORE it finishes — "… thinking" with no horizon
# reads like a hang.
TYPICAL_S = 1.8


def pending_line(elapsed: float, typical: float) -> FormattedText:
    """A horizon, not a spinner: elapsed, the expected total, and a bar."""
    typical = max(typical, 0.1)
    frac = min(elapsed / typical, 1.0)
    width = 18
    filled = int(frac * width)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    over = elapsed > typical * 1.25
    tail = f"{elapsed:4.1f}s / ~{typical:.0f}s"
    if over:
        tail = f"{elapsed:4.1f}s (longer than the usual {typical:.0f}s)"
    return FormattedText([
        ("class:jiffy.pending", f"{PENDING}  "),
        ("class:jiffy.pending" if not over else "class:jiffy.note", bar),
        ("class:jiffy.pending", f"  {tail}"),
    ])


def footer(ans: Answer | None, *, pending: bool = False,
           usd_cap: float = 0.0, error: str = "", n: int = 0, i: int = 0) -> FormattedText:
    """Judge · time · spend · degraded. The user never has to ask which judge answered
    or what it cost."""
    if ans is None and not error and not pending:
        return FormattedText([])      # nothing has been asked: say nothing, take no rows
    bits = [judge_that_answered(ans)]
    if ans is not None:
        bits.append(f"{ans.trace.total_ms / 1000:.1f}s")
        bits.append(f"${ans.trace.total_usd:.4f}")     # free and paid must never look alike
    if n > 1:
        bits.append(f"{i + 1}/{n}")
    parts: list[tuple[str, str]] = [("", MARGIN), ("class:jiffy.footer", SEP.join(bits))]
    if degraded(ans):
        parts.append(("class:jiffy.note", f"{SEP}degraded"))
    if pending:
        parts.append(("class:jiffy.pending", f"{SEP}{PENDING}"))
    if error:
        parts.append(("class:jiffy.error", f"{SEP}{error}"))
    return FormattedText(parts)
