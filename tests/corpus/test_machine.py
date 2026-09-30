"""The machine index is built from this machine and lives outside the package."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import jiffy
from jiffy.corpus import index, machine, tldr
from jiffy.types import Doc, Example

ON_PATH = {"ls", "git", "apt-get", "mv", "fd"}.__contains__


def which(name):
    return f"/usr/bin/{name}" if ON_PATH(name) else None


def test_a_man_page_counts_only_for_a_command_this_machine_has():
    assert machine.typed_name("ls", which) == "ls"
    assert machine.typed_name("apt-get", which) == "apt-get"      # on PATH itself: not a subcommand
    assert machine.typed_name("git-log", which) == "git log"      # typed, and titled by tldr, this way
    assert machine.typed_name("smartctl", which) is None


def test_whatis_keeps_sections_1_and_8_of_installed_commands():
    text = ("ls (1)               - list directory contents\n"
            "git-log (1)          - Show commit logs\n"
            "ls (1p)              - list directory contents (posix)\n"
            "printf (3)           - formatted output conversion\n"
            "smartctl (8)         - Control and Monitor Utility for SMART Disks\n")
    assert machine.whatis(text, which) == {"ls": "list directory contents", "git log": "Show commit logs"}


def test_builtins_parse_help_d():
    text = "cd - Change the shell working directory.\nhistory - Display or manipulate the history list.\n"
    assert machine.builtins(text) == {"cd": "Change the shell working directory.",
                                      "history": "Display or manipulate the history list."}


def test_merge_keeps_the_man_one_liner_beside_tldr_and_drops_uninstalled_pages():
    pages = {"mv": Doc("mv", "common", "Move or rename files and directories.", (Example("m", "mv a b"),), "tldr"),
             "fd": Doc("fd", "common", "Find entries.", (Example("f", "fd x"),), "tldr"),
             "7z": Doc("7z", "common", "Archiver.", (Example("x", "7z x a"),), "tldr")}
    docs = machine.merge({"mv": "move (rename) files", "ls": "list directory contents"},
                         {"cd": "Change the shell working directory."}, pages, which)
    assert docs["mv"].desc == "Move or rename files and directories. (move (rename) files)"
    assert docs["mv"].examples and docs["mv"].source == "tldr"
    assert docs["ls"].source == "man" and docs["ls"].examples == ()
    assert docs["cd"].source == "builtin"
    assert "fd" in docs and "7z" not in docs                     # installed without a man page; not installed


def test_load_warns_when_the_os_changed(tmp_path, monkeypatch):
    meta = {"os": {"ID": "ubuntu", "VERSION_ID": "22.04", "PRETTY_NAME": "Ubuntu 22.04"}}
    assert machine.stale(meta, {"ID": "ubuntu", "VERSION_ID": "22.04"}) == ""
    assert "run `jiffy index`" in machine.stale(meta, {"ID": "ubuntu", "VERSION_ID": "24.04",
                                                      "PRETTY_NAME": "Ubuntu 24.04"})
    (tmp_path / "machine.json").write_text(json.dumps({"meta": meta, "docs": {
        "ls": {"plat": "man", "desc": "list", "source": "man", "ex": []}}}))
    seen = []
    monkeypatch.setattr(machine, "os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "22.04"})
    assert machine.load(tmp_path, warn=seen.append)["ls"].desc == "list" and seen == []
    monkeypatch.setattr(machine, "os_release", lambda: {"ID": "debian", "VERSION_ID": "12"})
    machine.load(tmp_path, warn=seen.append)
    assert len(seen) == 1 and "Ubuntu 22.04" in seen[0]
    assert machine.load(tmp_path / "none") == {}


def test_os_release_reads_the_three_keys(tmp_path):
    p = tmp_path / "os-release"
    p.write_text('NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n')
    assert machine.os_release(p) == {"ID": "ubuntu", "VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04.1 LTS"}
    assert machine.os_release(tmp_path / "missing") == {}


def test_recipes_join_only_commands_the_machine_has(tmp_path):
    (tmp_path / "machine.json").write_text(json.dumps({"meta": {}, "docs": {
        "du": {"plat": "man", "desc": "estimate file space usage", "source": "man", "ex": []}}}))
    docs = index.load(("machine", "recipes"), corpus_dir=tmp_path)
    assert docs["du"].examples and "curl" not in docs


def _zip(pages: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in pages.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_tldr_pages_prefer_linux_and_drop_more_information(tmp_path):
    page = "# {n}\n\n> {d}\n> More information: <https://x>.\n\n- Do it:\n\n`{n} -x`\n"
    p = tmp_path / "t.zip"
    p.write_bytes(_zip({"common/free.md": page.format(n="free", d="common"),
                        "linux/free.md": page.format(n="free", d="linux"),
                        "osx/vm_stat.md": page.format(n="vm_stat", d="mac")}))
    pages = tldr.pages(p)
    assert set(pages) == {"free"}
    assert pages["free"].desc == "linux" and pages["free"].examples == (Example("Do it", "free -x"),)


def test_tldr_fetch_refuses_an_archive_that_is_not_the_pinned_release(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip({"common/x.md": "# x\n"}))
    with pytest.raises(ValueError, match="hash mismatch"):
        tldr.fetch(tmp_path / "cache", src=bad)
    assert not tldr.path(tmp_path / "cache").exists()


def test_the_package_ships_code_and_recipes_only():
    """Machine data, tldr pages and bench data stay out of what users install."""
    pkg = Path(jiffy.__file__).parent
    shipped = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts]
    assert [f for f in shipped if not (f.endswith(".py") or (f.startswith("corpus/recipes/")
                                                             and f.endswith(".md")))] == []
