from collections import Counter

from jendhub.bench.judgments import Judgment
from jendhub.bench.pooling import (
    agreement_rows,
    answer_pairs,
    format_agreement,
    pool,
    random_negatives,
)
from jendhub.bench.queries import Answer, Query

MAP = Query("map", "name", [Answer("List.map", 3), Answer("List.fold", 1)])
ADD = Query("add", "name", [Answer("Nat.add", 3)])
IDS = ["List.map", "List.fold", "Nat.add", "Nat.sub", "Nat.mul"]


class FixedPipeline:
    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings

    async def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return [self.rankings[query][:limit] for query in queries]


def keys(pairs: list[tuple[Query, str]]) -> list[tuple[str, str]]:
    return [(query.text, id_) for query, id_ in pairs]


async def test_pool_joins_the_top_results_of_each_pipeline_once() -> None:
    first = FixedPipeline({"map": ["List.map", "Nat.add", "Nat.sub"], "add": ["Nat.add"]})
    second = FixedPipeline({"map": ["Nat.add", "List.fold"], "add": ["Nat.mul"]})
    assert keys(await pool([first, second], [MAP, ADD], 2)) == [
        ("map", "List.map"),
        ("map", "Nat.add"),
        ("map", "List.fold"),
        ("add", "Nat.add"),
        ("add", "Nat.mul"),
    ]


def test_random_negatives_give_one_unanswered_definition_for_each_query() -> None:
    negatives = random_negatives([MAP, ADD], IDS)
    assert [query.text for query, _ in negatives] == ["map", "add"]
    assert all(id_ not in {a.id for a in query.answers} for query, id_ in negatives)


def test_random_negatives_are_the_same_on_each_run() -> None:
    assert keys(random_negatives([MAP, ADD], IDS)) == keys(random_negatives([MAP, ADD], IDS))


def test_answer_pairs_list_each_original_answer() -> None:
    assert keys(answer_pairs([MAP, ADD])) == [
        ("map", "List.map"),
        ("map", "List.fold"),
        ("add", "Nat.add"),
    ]


def test_agreement_rows_count_the_judged_grade_of_each_expected_grade() -> None:
    judgments = [
        Judgment("map", "List.map", 3),
        Judgment("map", "List.fold", 0),
        Judgment("add", "Nat.add", 2),
        Judgment("add", "Nat.sub", 0),
    ]
    negatives = [(ADD, "Nat.sub")]
    assert agreement_rows([MAP, ADD], negatives, judgments) == {
        "3": Counter({3: 1, 2: 1}),
        "2": Counter(),
        "1": Counter({0: 1}),
        "random": Counter({0: 1}),
    }


def test_format_agreement_prints_shares_and_the_summary() -> None:
    rows: dict[str, Counter[int]] = {
        "3": Counter({3: 3, 2: 1}),
        "2": Counter(),
        "1": Counter(),
        "random": Counter({0: 2}),
    }
    assert format_agreement(rows).splitlines() == [
        "expected  pairs  judged 0  judged 1  judged 2  judged 3",
        "3             4      0.00      0.00      0.25      0.75",
        "2             0      0.00      0.00      0.00      0.00",
        "1             0      0.00      0.00      0.00      0.00",
        "random        2      1.00      0.00      0.00      0.00",
        "same grade: 0.75, judged relevant: 1.00",
    ]
