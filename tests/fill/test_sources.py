"""Every source only offers, and only when it has evidence. Typed spans are strong; the rest wait for a judge."""
from __future__ import annotations
import pytest

from jiffy.fill.sources import (Sources, file_types, filenames, from_cwd, from_env, from_history, from_query,
                                mime_extensions, sizes)
from jiffy.fill.tokens import Kind

MIME = """\
# a comment line
text/x-python\t\t\tpy
text/markdown\t\t\tmd markdown
text/plain\t\t\ttxt text
application/x-sh\t\tsh
image/jpeg\t\t\tjpeg jpg
application/x-weird\t\t~ %
"""


@pytest.fixture
def mime(tmp_path):
    p = tmp_path / "mime.types"
    p.write_text(MIME)
    return p


def _values(cands):
    return [c.value for c in cands]


def _strong(cands):
    return [c.value for c in cands if c.strong]


def test_a_path_the_user_typed_is_strong_and_first():
    cands = from_query(Kind.DIR, "list files in ~/jiffy/tests")
    assert cands[0].value == "~/jiffy/tests" and cands[0].strong


def test_a_quoted_string_is_taken_at_face_value():
    assert "My Documents" in _strong(from_query(Kind.DIR, "open 'My Documents'"))


def test_no_place_is_read_from_words():
    """Code does not know what "home directory" means; the judge picks `~` from the anchors."""
    cands = from_query(Kind.DIR, "how much storage does the home directory take")
    assert _strong(cands) == [] and "~" not in _values(cands)


def test_the_shell_places_are_offered_weak_for_a_judge(tmp_path):
    s = Sources(cwd=tmp_path, home=tmp_path)
    cands = from_env(Kind.DIR, {}, "size of my home directory", s)
    assert {".", "~", "..", "/"} <= set(_values(cands))
    assert _strong(cands) == []                        # never filled without a judge
    assert from_env(Kind.FILE, {}, "anything", s) == []  # a file slot is not a place


def test_home_and_root_entries_are_offered_when_named(tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Music").mkdir()
    s = Sources(home=tmp_path, root=tmp_path)
    vals = _values(from_env(Kind.DIR, {}, "clean up my downloads", s))
    assert "~/Downloads" in vals and "/Downloads" in vals and "~/Music" not in vals


def test_mime_types_name_extensions(mime):
    assert file_types("list python files", mime) == ["py"]
    assert file_types("find the markdown files", mime) == ["md", "markdown"]
    assert file_types("search all text files", mime) == ["text", "txt"]    # an extension names its line
    assert file_types("find big files", mime) == []
    by_word, every = mime_extensions(mime)
    assert "~" not in every and "%" not in every                          # junk is not an extension


def test_an_absent_mime_file_is_no_table_not_a_crash(tmp_path):
    assert mime_extensions(tmp_path / "nope") == ({}, frozenset())
    assert file_types("list python files", tmp_path / "nope") == []
    assert from_query(Kind.EXT, "list python files", tmp_path / "nope") == []


def test_extensions_are_weak_proposals(mime):
    cands = from_query(Kind.EXT, "list python files in the home directory", mime)
    assert _values(cands) == ["py"] and _strong(cands) == []


def test_name_globs_are_built_from_every_shape_for_the_judge(mime):
    q = 'list markdown files in the home directory starting with the character "I"'
    vals = _values(from_query(Kind.PATTERN, q, mime))
    assert {"I*.md", "*.md", "I*", "*I", "*I*"} <= set(vals)
    assert "*.big" not in _values(from_query(Kind.PATTERN, "find big files", mime))
    assert "*_old" in _values(from_query(Kind.PATTERN, "files ending with _old", mime))


def test_a_typed_glob_is_strong(mime):
    assert _strong(from_query(Kind.PATTERN, "delete *.tmp here", mime))[0] == "*.tmp"


def test_a_typed_number_is_strong_and_number_words_are_weak():
    assert _values(from_query(Kind.NUMBER, "show the last 20 commits")) == ["20"]
    # a count in words is offered weak, only for words the request has: the judge picks, never invents
    assert _strong(from_query(Kind.NUMBER, "show the first ten lines")) == []
    assert _values(from_query(Kind.NUMBER, "show the first ten lines")) == ["1", "10"]
    assert from_query(Kind.NUMBER, "list files sorted by size") == []
    assert _strong(from_query(Kind.NUMBER, "split big.log into 100 megabyte pieces")) == []   # a size
    assert _values(from_query(Kind.PORT, "what is listening on port 8080")) == ["8080"]


@pytest.mark.parametrize("query,size", [
    ("find files bigger than 100 megabytes", "100M"),
    ("files over 2G", "2G"),
    ("smaller than 500kb", "500k"),
])
def test_sizes_carry_the_unit_and_offer_both_directions(query, size):
    assert sizes(query)[0][0] == size
    cands = from_query(Kind.SIZE, query)
    assert _strong(cands) == [size]
    assert {"+" + size, "-" + size} <= set(_values(cands))


def test_cwd_offers_only_what_is_really_there_and_named(tmp_path):
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "unrelated.txt").write_text("x")
    named = from_cwd(Kind.FILE, "count the lines in the readme", tmp_path)
    assert _values(named) == ["README.md"]
    assert from_cwd(Kind.FILE, "do something vague", tmp_path) == []


def test_cwd_respects_the_kind(tmp_path):
    (tmp_path / "build").mkdir()
    assert _values(from_cwd(Kind.DIR, "clean the build", tmp_path)) == ["build"]
    assert from_cwd(Kind.FILE, "clean the build", tmp_path) == []


def test_env_answers_facts_about_this_box():
    assert _values(from_env(Kind.USER, {"USER": "tex"})) == ["tex"]
    assert from_env(Kind.DIR, {"USER": "tex"}) == []


def test_a_real_user_named_in_the_request_is_offered(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0::/root:/bin/sh\nbob:x:1000:1000::/home/bob:/bin/sh\n")
    cands = from_env(Kind.USER, {"USER": "tex"}, "files owned by bob", Sources(root=tmp_path))
    assert _values(cands) == ["tex", "bob"] and not cands[1].strong


def test_history_needs_a_whole_component_and_a_live_path(tmp_path):
    real = tmp_path / "projects"
    real.mkdir()
    hist = (f"cd {real}", "tail /var/log/syslog")
    assert _values(from_history(Kind.DIR, "list my projects", hist)) == [str(real)]
    # `log` appears inside `/var/log/syslog`, but that is a file, and the token wants a directory
    assert from_history(Kind.DIR, "find big log files", hist) == []


def test_sources_are_inert_by_default():
    """A bare `Sources()` reads nothing, which is what the unit tests rely on."""
    empty = Sources()
    assert empty.cwd is None and empty.env is None and empty.history == () and empty.mime is None


def test_live_sources_never_write(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    Sources.live()
    assert list(tmp_path.iterdir()) == []


def test_a_quoted_string_fills_paths_and_hosts_but_not_structured_slots():
    """`'~/launchpad'` is a path, not a depth; `'google.com'` is a host, not a port."""
    q = "show 2 level tree of '~/launchpad'"
    for kind in (Kind.NUMBER, Kind.PORT, Kind.SIZE, Kind.EXT):
        assert "~/launchpad" not in _values(from_query(kind, q))
    assert _strong(from_query(Kind.DIR, q)) == ["~/launchpad"]
    assert "google.com" in _values(from_query(Kind.HOST, "check if 'google.com' is reachable"))


def test_a_name_after_called_or_named_fills_a_path_slot():
    """`called demo-project` names the directory; the slot must take the name."""
    assert _strong(from_query(Kind.DIR, "create a new directory called demo-project")) == ["demo-project"]
    assert _strong(from_query(Kind.FILE, "make a script named deploy")) == ["deploy"]


def test_a_bare_file_name_fills_a_file_slot_but_not_a_directory(mime):
    q = "show the most common lines in words.txt"
    assert _strong(from_query(Kind.FILE, q, mime)) == ["words.txt"]
    assert _strong(from_query(Kind.DIR, q, mime)) == []
    assert filenames("ping google.com", mime) == []
    assert filenames("ping google.com", None) == []                     # no table, no file names


def test_plain_words_are_weak_and_never_fill_a_structured_slot():
    assert "htop" in _values(from_query(Kind.UNKNOWN, "install htop"))
    assert "password" in _values(from_query(Kind.PATTERN, "search for the word password"))
    assert _strong(from_query(Kind.UNKNOWN, "install htop")) == []
    assert _strong(from_query(Kind.DIR, "install htop")) == []          # a word is only ever a hint
    assert from_query(Kind.NUMBER, "install htop") == []


def test_sensor_chips_are_the_ones_this_machine_has(tmp_path):
    """`sensors {{chip}}` for a GPU request: offer the kernel's chips, never the word `gpu`."""
    for i, name in enumerate(["k10temp", "amdgpu", "nvme", "nvme"]):
        (tmp_path / f"sys/class/hwmon/hwmon{i}").mkdir(parents=True)
        (tmp_path / f"sys/class/hwmon/hwmon{i}/name").write_text(name + "\n")
    cands = from_env(Kind.CHIP, {}, "check gpu temperatures", Sources(root=tmp_path))
    assert _values(cands) == ["k10temp-*", "amdgpu-*", "nvme-*"]
    assert not any(c.strong for c in cands)
    assert from_query(Kind.CHIP, "check gpu temperatures") == []


def test_no_hwmon_is_no_chips(tmp_path):
    assert from_env(Kind.CHIP, {}, "gpu temps", Sources(root=tmp_path)) == []


def test_a_separator_typed_is_strong_and_the_common_ones_are_offered():
    assert _strong(from_query(Kind.DELIM, "split each line on ; please")) == ["';'"]
    assert _strong(from_query(Kind.DELIM, "fields separated by colons")) == []
    assert _values(from_query(Kind.DELIM, "fields separated by colons")) == [":"]
    assert from_query(Kind.DELIM, "cut the first field") == []
