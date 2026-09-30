from jiffy.corpus.gloss import first_sentence, gloss
from jiffy.corpus.options import Option, Spec

SPECS = {
    "ls": Spec("ls", options=(Option(("-a", "--all"), desc="do not ignore entries starting with ."),
                              Option(("-l",), desc="use a long listing format"),
                              Option(("-h", "--human-readable"), desc="print sizes like 1K. More text."))),
    "tree": Spec("tree", options=(Option(("-L",), "level", desc="Max display depth of the directory tree."),)),
    "git log": Spec("git log", options=(Option(("-n", "--max-count"), "<number>", joiner="=",
                                               desc="Limit the number of commits"),)),
    "sort": Spec("sort", options=(Option(("-h",), desc="compare human readable numbers"),)),
    "head": Spec("head", options=(Option(("-n", "--lines"), "NUM", desc="print the first NUM lines"),)),
}


def test_a_flag_with_an_argument_is_shown_with_it():
    assert gloss("tree -L 2 ~", SPECS) == [("-L 2", "Max display depth of the directory tree")]


def test_a_cluster_is_each_of_its_flags():
    assert [f for f, _ in gloss("ls -lah ~", SPECS)] == ["-l", "-a", "-h"]


def test_a_glued_value_and_an_equals_value():
    assert gloss("head -n20 x", SPECS)[0][0] == "-n20"
    assert gloss("git log --max-count=5", SPECS)[0][0] == "--max-count=5"


def test_two_word_commands_and_pipes_use_each_segments_own_page():
    assert gloss("git log -n 3", SPECS) == [("-n 3", "Limit the number of commits")]
    assert [w for _, w in gloss("ls -h | sort -h 2>/dev/null", SPECS)] == [
        "print sizes like 1K", "compare human readable numbers"]


def test_unknown_commands_and_flags_are_left_unexplained():
    assert gloss("frobnicate -x", SPECS) == []
    assert gloss("ls -Z", SPECS) == []
    assert gloss("sudo ls -a", SPECS)[0][0] == "-a"


def test_an_unclosed_quote_while_typing_does_not_raise():
    assert gloss("ls -l 'half", SPECS)[0][0] == "-l"


def test_first_sentence_keeps_a_meaningful_dot():
    assert first_sentence("do not ignore entries starting with .") == "do not ignore entries starting with ."
    assert first_sentence("Use  archive   file.  Then more") == "Use archive file"
