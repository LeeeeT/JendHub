import pytest

from jendhub.indexing.failures import (
    LoadError,
    Place,
    Problem,
    errors_json,
    parse_errors,
    problems,
    summary,
)

DUPLICATE = LoadError(
    "a fresh constructor name (duplicate declaration: Wait)",
    "'Wait'",
    Place("ezx@1.5.0.0/src/hub.bend", 87),
)
MISSING_AT_A = LoadError("a package named codec@0.2.0.0", None, Place("kit@1.0.0.0/a.bend", 3))
MISSING_AT_B = LoadError("a package named codec@0.2.0.0", None, Place("kit@1.0.0.0/b.bend", 4))


def test_files_that_stop_at_one_error_are_one_problem() -> None:
    errors = {"main.bend": DUPLICATE, "src/hub.bend": DUPLICATE, "src/run.bend": DUPLICATE}
    assert problems(errors) == [
        Problem(
            DUPLICATE.expected,
            DUPLICATE.observed,
            [Place("ezx@1.5.0.0/src/hub.bend", 87)],
            ["main.bend", "src/hub.bend", "src/run.bend"],
        )
    ]


def test_one_error_at_two_places_is_one_problem_with_both_places() -> None:
    found = problems({"a.bend": MISSING_AT_A, "b.bend": MISSING_AT_B, "c.bend": MISSING_AT_A})
    assert [problem.places for problem in found] == [
        [Place("kit@1.0.0.0/a.bend", 3), Place("kit@1.0.0.0/b.bend", 4)]
    ]
    assert found[0].files == ["a.bend", "b.bend", "c.bend"]


def test_the_problem_with_the_most_files_comes_first() -> None:
    found = problems({"a.bend": MISSING_AT_A, "b.bend": DUPLICATE, "c.bend": DUPLICATE})
    assert [problem.files for problem in found] == [["b.bend", "c.bend"], ["a.bend"]]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (DUPLICATE, "expected a fresh constructor name (duplicate declaration: Wait)"),
        (MISSING_AT_A, "expected a package named codec@0.2.0.0"),
        (LoadError("a term", "')'", None), "expected a term, got ')'"),
    ],
)
def test_a_message_names_the_observed_text_only_when_it_adds_to_it(
    error: LoadError, message: str
) -> None:
    assert problems({"main.bend": error})[0].message == message


def test_errors_survive_json() -> None:
    errors = {"main.bend": DUPLICATE, "x.bend": LoadError("a term", None, None)}
    assert parse_errors(errors_json(errors)) == errors


def test_a_summary_counts_the_files_of_each_problem() -> None:
    assert summary({"a.bend": DUPLICATE, "b.bend": DUPLICATE}) == (
        "expected a fresh constructor name (duplicate declaration: Wait) (2 files)"
    )
