from jendhub.search.engine import best_by, module_key
from jendhub.vectors.index import Neighbor


def test_best_by_keeps_the_best_score_of_each_module() -> None:
    neighbors = [
        Neighbor(1, 0.9, "base", "a"),
        Neighbor(2, 0.7, "kit", "a"),
        Neighbor(3, 0.8, "base", "a"),
        Neighbor(4, 0.6, "kit", "b"),
    ]
    assert best_by(neighbors, map(module_key, neighbors)) == {
        ("base", "a"): 0.9,
        ("kit", "a"): 0.7,
        ("kit", "b"): 0.6,
    }
    assert best_by(neighbors, (neighbor.release for neighbor in neighbors)) == {
        "base": 0.9,
        "kit": 0.7,
    }
