"""What a `{{...}}` token actually asks for.

tldr writes three different things inside the same braces, and conflating them
is why jiffy asked you to type `filesystem`:

    {{[-s|--summarize]}}   a SPELLING. Both alternatives are already correct;
                           nothing is missing. Resolved with no source at all.
    {{path/to/directory}}  a VALUE. Something only the user knows.
    {{name|uuid}}          a value with two shapes. Still a value.

and jiffy adds one of its own: `{{?path/to/directory}}` is an OPTIONAL value
(a positional compose may leave out). Unfilled, it is dropped, not shown.

`parse` sorts them, and `classify` says what KIND of value is wanted so a
source knows whether to offer a directory, a filename or a username.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = ["Kind", "Token", "parse", "classify", "ALTERNATION", "PLACEHOLDER"]

#: The one definition of what a token looks like. It lives here, at the bottom of
#: the import graph, because `jiffy.graph`, `jiffy.tui` and `jiffy.fill` all need
#: it and only one of them may own it.
PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")
ALTERNATION = re.compile(r"^\[(.+)\]$")          # {{[-s|--summarize]}}


class Kind(str, Enum):
    """What sort of value fits. `SPELLING` needs no value at all."""

    SPELLING = "spelling"
    DIR = "dir"
    FILE = "file"
    PATH = "path"          # file or directory, the token did not say
    PATTERN = "pattern"
    USER = "user"
    HOST = "host"
    PORT = "port"
    URL = "url"
    NUMBER = "number"
    EXT = "ext"            # a bare extension: `fd -e {{txt}}` wants `py`, not `*.py`
    SIZE = "size"          # `find -size {{+500k}}`, `split -b {{size}}`: a number with a unit
    CHIP = "chip"          # `sensors {{chip}}`: a hardware sensor chip this machine has
    DELIM = "delim"        # `cut -d {{delim}}`, `paste -d {{delimiter}}`: one separator character
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Token:
    """One `{{...}}`, parsed."""

    raw: str                       # the inner text, without braces
    kind: Kind
    options: tuple[str, ...] = ()  # SPELLING only: the alternatives, as written
    optional: bool = False         # `{{?...}}`: compose's optional positional; unfilled, it is dropped

    @property
    def spelling(self) -> bool:
        return self.kind is Kind.SPELLING


#: exact token -> kind. Short tokens MUST live here: as substrings `n` matches
#: `name`, `port` matches `report`, and the classifier would be nonsense.
_EXACT: dict[str, Kind] = {
    "n": Kind.NUMBER, "num": Kind.NUMBER, "x": Kind.NUMBER, "k": Kind.NUMBER,
    "level": Kind.NUMBER, "levels": Kind.NUMBER, "layer": Kind.NUMBER,
    "layers": Kind.NUMBER, "limit": Kind.NUMBER, "width": Kind.NUMBER,
    "height": Kind.NUMBER, "seconds": Kind.NUMBER, "minutes": Kind.NUMBER,
    "pid": Kind.NUMBER, "process_id": Kind.NUMBER,
    "url": Kind.URL, "example.com": Kind.URL, "https://example.com": Kind.URL,
    "http://example.com": Kind.URL, "address": Kind.URL,
    "dir": Kind.DIR, "dirs": Kind.DIR,
    "chip": Kind.CHIP, "chips": Kind.CHIP,
    "delim": Kind.DELIM, "sep": Kind.DELIM, "fs": Kind.DELIM,
}

#: substring -> kind, first match wins, so longer and more specific come first
_RULES: tuple[tuple[str, Kind], ...] = (
    ("delimiter", Kind.DELIM), ("delimiters", Kind.DELIM), ("delim", Kind.DELIM),
    ("separator", Kind.DELIM),
    ("path/to/directory", Kind.DIR), ("path/to/folder", Kind.DIR),
    ("path/to/file", Kind.FILE), ("path/to", Kind.PATH),
    ("extension", Kind.EXT), ("ext", Kind.EXT),          # before "file": `file_extension` is an EXT
    ("directory", Kind.DIR), ("folder", Kind.DIR), ("dirname", Kind.DIR),
    ("filename", Kind.FILE), ("file", Kind.FILE),
    ("username", Kind.USER), ("user", Kind.USER), ("owner", Kind.USER),
    ("hostname", Kind.HOST), ("host", Kind.HOST), ("remote", Kind.HOST),
    ("ip", Kind.HOST), ("domain", Kind.HOST),
    ("port", Kind.PORT),
    ("pattern", Kind.PATTERN), ("regex", Kind.PATTERN), ("glob", Kind.PATTERN),
    ("search", Kind.PATTERN),
    ("number", Kind.NUMBER), ("count", Kind.NUMBER), ("size", Kind.SIZE),
    ("depth", Kind.NUMBER), ("lines", Kind.NUMBER), ("_id", Kind.NUMBER),
    ("url", Kind.URL),
    ("dir", Kind.DIR),                       # after "dirname", before nothing
    ("path", Kind.PATH),
)


#: `+500k`, `-10M`, `1G`: a signed or unit-suffixed size, as find/split/du spell them
_SIZE_SHAPE = re.compile(r"^(?:[+-]\d+(?:\.\d+)?[kmgtc]?|\d+(?:\.\d+)?[kmgt])$")

#: file extensions common enough to recognise, bare. Shared with `sources`.
EXTENSIONS = frozenset(
    "txt md py js ts jsx tsx rs go c h cpp hpp cc java kt rb pl php sh bash zsh lua hs swift "
    "json yaml yml toml ini conf cfg xml html htm css csv tsv log sql "
    "jpg jpeg png gif svg webp bmp tiff mp3 wav flac ogg mp4 mkv avi mov webm "
    "pdf doc docx xls xlsx ppt pptx odt zip tar gz tgz bz2 xz 7z rar iso deb rpm tmp bak".split())


def _parts(token: str) -> set[str]:
    """The words a token is made of. `report` is not a `port`."""
    return {p for p in re.split(r"[^a-z0-9]+", token) if p}


def classify(raw: str) -> Kind:
    """The kind of value a token wants, from how tldr named it.

    Matching is on whole word-parts, not substrings: `report` contains `port`
    and `filename` contains `name`, and a classifier that reads those as a port
    and a username fills them with confident nonsense. A rule that spans words
    (`path/to/file`) is matched against the whole token instead.
    """
    low = raw.strip().lower()
    if low.startswith("*.") or low.endswith("*") or "*" in low:
        return Kind.PATTERN
    if _SIZE_SHAPE.match(low):
        return Kind.SIZE
    if low.isdigit():
        return Kind.NUMBER
    if low in EXTENSIONS:                    # tldr names an extension slot by an example of one
        return Kind.EXT
    head = low.split("|")[0]                  # {{name|uuid}} is named by its first shape
    if low in _EXACT:
        return _EXACT[low]
    if head in _EXACT:
        return _EXACT[head]
    words = _parts(head) | _parts(low)
    for needle, kind in _RULES:
        if not needle.isalnum():             # a multi-word rule: match the whole token
            if needle in head or needle in low:
                return kind
        elif needle in words:
            return kind
    return Kind.UNKNOWN


def parse(raw: str) -> Token:
    """One token's inner text -> what it is asking for."""
    inner = raw.strip()
    if inner.startswith("?"):          # tldr never writes `{{?`; only compose does
        inner = inner[1:].strip()
        return Token(inner, classify(inner), optional=True)
    alt = ALTERNATION.match(inner)
    if alt:
        options = tuple(o.strip() for o in alt.group(1).split("|") if o.strip())
        if len(options) > 1:
            return Token(inner, Kind.SPELLING, options)
    return Token(inner, classify(inner))


def shortest(options: tuple[str, ...]) -> str:
    """The short spelling. Ties break on the order tldr wrote them."""
    return min(options, key=lambda o: (len(o), options.index(o))) if options else ""
