"""tldr pages from a pinned upstream release, verified, cached, parsed in memory.

`fetch` downloads the English page archive once into the corpus cache and checks its
sha256; `pages` reads it. Nothing from tldr lands in the repo or the package: the zip
lives under `~/.local/share/jiffy/corpus/`, and `jiffy index` decides which pages
describe commands this machine actually has (`machine.build`).
"""
from __future__ import annotations

import hashlib
import urllib.request
import zipfile
from pathlib import Path

from jiffy.config import CORPUS_DIR
from jiffy.types import Doc, Example

VERSION = "v2.3"
URL = f"https://github.com/tldr-pages/tldr/releases/download/{VERSION}/tldr-pages.en.zip"
SHA256 = "c6cb2959801235f9b1a5e66af74988209f8c9a6dc1cb6e8c6e8721461bc73c9a"
PLATFORMS = ("common", "linux")    # linux wins where both have a page
MAX_EX = 8                         # examples kept per page


def path(corpus_dir: Path = CORPUS_DIR) -> Path:
    return corpus_dir / f"tldr-{VERSION}.en.zip"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fetch(corpus_dir: Path = CORPUS_DIR, src: Path | None = None, timeout: float = 60.0) -> Path:
    """The verified archive in the cache. Downloads it if absent, or copies `src`
    (an offline copy of the same release). A hash mismatch is an error, never a guess."""
    dst = path(corpus_dir)
    if dst.exists() and _sha256(dst) == SHA256:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    if src is not None:
        tmp.write_bytes(Path(src).read_bytes())
    else:
        with urllib.request.urlopen(URL, timeout=timeout) as r:
            tmp.write_bytes(r.read())
    got = _sha256(tmp)
    if got != SHA256:
        tmp.unlink()
        raise ValueError(f"tldr archive hash mismatch: got {got}, pinned {SHA256} ({VERSION})")
    tmp.replace(dst)
    return dst


def parse(text: str, plat: str = "common", source: str = "tldr") -> Doc | None:
    """One tldr-format page -> Doc, or None without a title or an example."""
    name, desc, ex, pend = "", [], [], ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
        elif line.startswith(">"):
            body = line[1:].strip()
            if body and not body.startswith("More information"):
                desc.append(body)
        elif line.startswith("- "):
            pend = line[2:].strip().rstrip(":")
        elif line.startswith("`") and line.endswith("`") and len(line) > 1 and pend:
            ex.append(Example(pend, line[1:-1]))
            pend = ""
    if not name or not ex:
        return None
    return Doc(name, plat, " ".join(desc), tuple(ex[:MAX_EX]), source)


def pages(archive: Path) -> dict[str, Doc]:
    """Every common and linux page in the archive, keyed by page title (`git log`)."""
    out: dict[str, Doc] = {}
    with zipfile.ZipFile(archive) as z:
        for plat in PLATFORMS:
            for n in z.namelist():
                if n.startswith(plat + "/") and n.endswith(".md"):
                    d = parse(z.read(n).decode("utf-8", "replace"), plat)
                    if d is not None:
                        out[d.name] = d
    return out
