from jendhub.bench.pooling import answer_pairs, pool
from jendhub.bench.queries import Answer, Query

MAP = Query("map", "name", [Answer("List.map", 3), Answer("List.fold", 1)])
ADD = Query("add", "name", [Answer("Nat.add", 3)])


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


def test_answer_pairs_list_each_original_answer() -> None:
    assert keys(answer_pairs([MAP, ADD])) == [
        ("map", "List.map"),
        ("map", "List.fold"),
        ("add", "Nat.add"),
    ]
