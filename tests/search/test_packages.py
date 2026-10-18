import pytest

from jendhub.search.packages import DefinitionSummary


@pytest.mark.parametrize(
    ("head", "summary"),
    [
        ("# Maps each item.\ndef map(xs):", "Maps each item."),
        (
            "# Num keeps the exact text, so no precision\n# is lost. Obj keeps order.\ntype Val:",
            "Num keeps the exact text, so no precision is lost.",
        ),
        ("#\n# One code point in UTF-8\n#\n# More.\ndef cp(c):", "One code point in UTF-8"),
        ("# Version 1.2 of it\ndef v():", "Version 1.2 of it"),
        ("def bare(x):", None),
    ],
)
def test_a_summary_is_the_first_sentence_of_the_doc_comment(head: str, summary: str | None) -> None:
    assert DefinitionSummary("kit@1.0.0.0/main:f", "def", "f", "main", head).summary == summary
