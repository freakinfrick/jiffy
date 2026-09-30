from jiffy.corpus import recipes
from jiffy.types import Doc, Example

PAGE = """\
# curl

> Recipes jiffy adds.

- Show this machine's public IP address:

`curl -s https://ifconfig.me`
"""


def test_a_tldr_format_page_parses():
    d = recipes.parse(PAGE)
    assert d.name == "curl" and d.source == "recipe"
    assert d.examples == (Example("Show this machine's public IP address", "curl -s https://ifconfig.me"),)
    assert recipes.parse("# empty\n\n> nothing here\n") is None


def test_recipes_go_first_on_an_existing_page_and_make_a_missing_one():
    tldr = {"curl": Doc("curl", "common", "Transfer data.", (Example("Get a page", "curl {{url}}"),), "tldr")}
    out = recipes.merge(tldr, {"curl": recipes.parse(PAGE), "new": recipes.parse(PAGE.replace("# curl", "# new"))})
    assert [e.line for e in out["curl"].examples] == ["curl -s https://ifconfig.me", "curl {{url}}"]
    assert out["curl"].source == "tldr" and out["new"].source == "recipe"
    assert recipes.merge(out, {"curl": recipes.parse(PAGE)})["curl"].examples == out["curl"].examples


def test_every_shipped_recipe_parses():
    shipped = sorted(recipes.DIR.glob("*.md"))
    assert shipped and all(recipes.parse(p.read_text()) is not None for p in shipped)
