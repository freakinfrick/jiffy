"""Fill the `{{...}}` tokens from what the user already said.

One rule decides every hard case: **code proposes, the judge only picks.** The
sources offer values (`jiffy.fill.sources`); the judge, in one batch, picks ONE
proposal per token or none; this module writes the string. No model is ever
asked to produce a path.

The second rule is that an unresolved token stays a token. Nothing here invents
a value, so the guarantee the TUI depends on — a line that still shows a token
cannot run — is never quietly satisfied by a guess. With no judge (or a failed
one) only a `strong` proposal, a span the user typed verbatim, may fill.

Ordering matters upstream: this runs BEFORE the gate, so the gate judges
`rm -rf ~` rather than `rm -rf {{path/to/directory}}`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import re

from jiffy.types import Fill

from .sources import PATH_KINDS, Candidate, Sources, from_cwd, from_env, from_history, from_query
from .tokens import PLACEHOLDER, Kind, Token, parse, shortest

__all__ = ["resolve", "Filled", "Sources", "candidates_for", "Fill", "Pick", "Ask", "Choices"]

#: one token and what is on offer for it
Ask = tuple[Token, list[Candidate]]
#: (query, line, asks) -> for each ask, the index of the chosen candidate, or None for "none of these"
Pick = Callable[[str, str, Sequence[Ask]], Sequence["int | None"]]
#: token text -> the only values it may take: compose's flag arguments, from the man page.
#: An empty tuple is a closed blank: nothing is offered, the user types it.
Choices = Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class Filled:
    """The line after filling, and an account of every token in it."""

    line: str
    fills: tuple[Fill, ...] = ()

    @property
    def unresolved(self) -> tuple[Fill, ...]:
        return tuple(f for f in self.fills if not f.value)


#: characters the shell would act on if a value carried them bare
_NEEDS_QUOTES = re.compile(r"[*?\[\]\s$`\\|&;<>()]")

#: a `-n`/`-lines` count argument on `head`/`tail`, both of which default to 10.
_COUNT_FLAG = re.compile(r"(?:head|tail)\s+-(?:n|lines)\s*$")
_HEAD_DEFAULT = "10"

#: a glob in a path slot is the shell's to expand (`ls ~/*.md`): quoting it would break that
_SHELL_GLOB = re.compile(r"[~\w./*?+-]+")


def quote(value: str, line: str, start: int, end: int) -> str:
    """Quote a value the shell would otherwise interpret — unless tldr already did.

    `find . -name '{{*.ext}}'` ships its own quotes, so adding more would search
    for a literal `'*.log'`. A bare `{{*.ext}}` does not, and an unquoted glob is
    expanded by the shell before the command ever sees it. `~` is the exception
    in the other direction: quoting it stops the expansion the user wants.
    """
    if not value or not _NEEDS_QUOTES.search(value):
        return value
    before = line[start - 1] if start else ""
    after = line[end] if end < len(line) else ""
    if before and before == after and before in "'\"":     # tldr already quoted the token
        return value
    if "'" in value:
        return value
    return f"'{value}'"


def _listed(values: Sequence[str], query: str) -> list[Candidate]:
    """A closed blank's values, the ones the request mentions first. Only a judge may pick one."""
    said = {w.lower() for w in re.findall(r"[\w.+-]+", query)}
    out = [Candidate(v, "manual", "listed in the manual", False) for v in values]
    return sorted(out, key=lambda c: not ({c.value.lower(), c.value.lower().rstrip("s")} & said))


def candidates_for(token: Token, query: str, sources: Sources, choices: Choices | None = None) -> list[Candidate]:
    """Every value on offer for one token, best source first, deduped.

    A token in `choices` is closed: its listed values, or nothing. `nvidia-smi -d
    {{type}}` is offered MEMORY, TEMPERATURE, ...; never `gpu` because the request said it.
    """
    if token.spelling:
        return []
    if choices is not None and token.raw in choices:
        return _listed(choices[token.raw], query)
    out: list[Candidate] = []
    for group in (from_query(token.kind, query, sources.mime),
                  from_cwd(token.kind, query, sources.cwd),
                  from_env(token.kind, sources.env, query, sources),
                  from_history(token.kind, query, sources.history)):
        out += [c for c in group if c.value not in {o.value for o in out}]
    if token.kind in (Kind.FILE, Kind.PATH):
        # `ls ~/*.py`: a type the request names, in each place on offer
        types = [c for c in out if c.value.startswith("*.") and not c.strong]
        if types:
            places = candidates_for(Token(token.raw, Kind.DIR), query, sources)
            out += [Candidate(f"{p.value.rstrip('/')}/{t.value}", "query", f"{t.why}, in {p.value}", False)
                    for p in places if p.value != "." and (p.strong or p.source != "query") for t in types]
    if token.kind is Kind.SIZE and token.raw[:1] in "+-":
        # `-size {{+500k}} -size {{-10M}}` is a lower and an upper bound. A size signed
        # the other way would flip the bound's meaning; the token's own sign goes first.
        opposite = "-" if token.raw[0] == "+" else "+"
        out = [c for c in out if not c.value.startswith(opposite)]
        out.sort(key=lambda c: not c.value.startswith(token.raw[0]))
    return out


def _code_only(cands: list[Candidate]) -> Candidate | None:
    """Without a judge, only a value the user typed verbatim (or a fact) may fill."""
    return next((c for c in cands if c.strong), None)


def resolve(line: str, query: str, sources: Sources | None = None,
            pick: Pick | None = None, choices: Choices | None = None) -> Filled:
    """Replace what the request already answers; leave the rest alone.

    `pick` is called once, for every token that has a proposal: the one judgement
    call here, and it is a choice among strings code built (or none of them).
    If it fails, the code-only choice stands.
    """
    sources = sources or Sources()
    matches = list(PLACEHOLDER.finditer(line))
    tokens = [parse(m.group(1)) for m in matches]
    cands = [candidates_for(t, query, sources, choices) for t in tokens]
    chosen: list[Candidate | None] = [_code_only(c) for c in cands]
    asked = [i for i, (t, c) in enumerate(zip(tokens, cands)) if c and not t.spelling]
    if pick is not None and asked:
        try:
            picks = list(pick(query, line, [(tokens[i], cands[i]) for i in asked]))
            if len(picks) != len(asked):
                raise ValueError(f"{len(picks)} picks for {len(asked)} tokens")
            for i, k in zip(asked, picks):
                chosen[i] = None if k is None else cands[i][max(0, min(int(k), len(cands[i]) - 1))]
        except Exception:                     # a judge failure: the code-only choice stands
            chosen = [_code_only(c) for c in cands]

    out, fills, pos = [], [], 0
    for m, token, c in zip(matches, tokens, chosen):
        head = line[pos:m.start()]
        if token.spelling:                    # both spellings are correct; take the short one
            value, source, why = shortest(token.options), "spelling", "either spelling is correct"
        elif c is not None:
            bare = token.kind in PATH_KINDS and _SHELL_GLOB.fullmatch(c.value)
            value = c.value if bare else quote(c.value, line, *m.span())
            source, why = c.source, c.why
        elif token.kind is Kind.NUMBER and _COUNT_FLAG.search(line[:m.start()]):
            value, source, why = _HEAD_DEFAULT, "default", "head defaults to 10"
        elif token.optional:                  # compose's optional positional: nothing said it, so leave it out
            out.append(head[:-1] if head.endswith(" ") else head)
            pos = m.end()
            continue
        else:                                 # nothing said it: it stays a token
            value, source, why = m.group(0), "", "nothing in the request names it"
        out.append(head)
        start = sum(map(len, out))
        fills.append(Fill(token.raw, value if source else "", source, why, start, start + len(value)))
        out.append(value)
        pos = m.end()
    out.append(line[pos:])
    return Filled("".join(out), tuple(fills))
