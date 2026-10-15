from pathlib import Path

from jendhub.bench import Answer, Query
from jendhub.judgments import Assessment, Judgment, JudgmentStore, assessments

MAP = Query("map a list", "phrase", [Answer("List.map", 3)])


def test_the_store_keeps_each_judgment_under_its_request(tmp_path: Path) -> None:
    store = JudgmentStore(tmp_path / "judgments.sqlite")
    store.store(b"a", Judgment("map a list", "List.fold", 1))
    store.store(b"b", Judgment("map a list", "List.fold", 2))
    store.store(b"a", Judgment("map a list", "List.fold", 0))
    assert JudgmentStore(tmp_path / "judgments.sqlite").load() == {
        b"a": Judgment("map a list", "List.fold", 0),
        b"b": Judgment("map a list", "List.fold", 2),
    }


def test_assessments_keep_the_original_grade_over_a_judgment() -> None:
    judgments = [Judgment("map a list", "List.map", 1), Judgment("map a list", "List.fold", 0)]
    assert assessments([MAP], judgments) == [
        Assessment("map a list", "phrase", {"List.fold": 0, "List.map": 3})
    ]
