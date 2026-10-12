import pytest

from jendhub.tokens import tokens


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("map a list", ["map", "a", "list"]),
        ("List.map", ["list.map", "list", "map"]),
        ("checked_add", ["checked_add", "checked", "add"]),
        ("parseJson", ["parsejson", "parse", "json"]),
        ("get.u32le", ["get.u32le", "get", "u32le", "u32", "le"]),
        ("U32", ["u32"]),
        ("HTTPServer", ["httpserver", "http", "server"]),
        ("(s: String) -> Maybe<Nat>", ["s", "string", "maybe", "nat"]),
        ("the end.", ["the", "end"]),
        ("bend-kit-json", ["bend-kit-json", "bend", "kit", "json"]),
        ("-A: Kind(a)", ["a", "kind", "a"]),
    ],
)
def test_tokens_split_identifiers_and_keep_the_whole_word(text: str, expected: list[str]) -> None:
    assert tokens(text) == expected
