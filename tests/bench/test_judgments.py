from jendhub.bench.judgments import Assessment, Judgment, JudgmentStore, assessments
from jendhub.bench.queries import Answer, Query
from jendhub.db.pool import Pool

MAP = Query("map a list", "phrase", [Answer("List.map", 3)])


async def test_the_store_keeps_each_judgment_under_its_request(pool: Pool) -> None:
    store = JudgmentStore(pool)
    await store.store(b"a", Judgment("map a list", "List.fold", 1))
    await store.store(b"b", Judgment("map a list", "List.fold", 2))
    await store.store(b"a", Judgment("map a list", "List.fold", 0))
    assert await JudgmentStore(pool).load() == {
        b"a": Judgment("map a list", "List.fold", 0),
        b"b": Judgment("map a list", "List.fold", 2),
    }


def test_assessments_keep_the_original_grade_over_a_judgment() -> None:
    judgments = [Judgment("map a list", "List.map", 1), Judgment("map a list", "List.fold", 0)]
    assert assessments([MAP], judgments) == [
        Assessment("map a list", "phrase", {"List.fold": 0, "List.map": 3})
    ]
