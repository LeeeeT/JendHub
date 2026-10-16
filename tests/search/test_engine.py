import math
from collections import Counter

import numpy as np
import pytest

from jendhub.search.engine import WEIGHTS, Candidate, Query, reached_gain, score
from jendhub.search.signatures import Signature


def candidate(*, reached: list[int], users: int = 0, helper: bool = False) -> Candidate:
    return Candidate(
        1,
        "List.map",
        "def",
        "List.map",
        None,
        None,
        "",
        "def List.map(xs: List) -> List:",
        Signature([Counter({"List": 1})], Counter({"List": 1})),
        documented=True,
        helper=helper,
        users=users,
        external=True,
        reached=reached,
    )


def test_reached_gain_uses_the_best_reached_similarity_in_the_pool() -> None:
    assert reached_gain(candidate(reached=[2, 3]), 0.5, {2: 0.7, 3: 0.6}, 0.1) == pytest.approx(0.2)


def test_reached_gain_falls_back_to_the_pool_floor() -> None:
    assert reached_gain(candidate(reached=[9]), 0.5, {2: 0.7}, 0.3) == pytest.approx(-0.2)


def test_reached_gain_is_zero_without_reached_definitions() -> None:
    assert reached_gain(candidate(reached=[]), 0.5, {}, 0.3) == 0.0


def test_score_adds_the_weighted_features() -> None:
    query = Query("List -> List", np.zeros(1, dtype=np.float32), {"list"}, None)
    found = score(query, candidate(reached=[], users=1), 0.5, {}, 0.0)
    expected = (
        0.5
        + WEIGHTS.name * 1 / 3
        + WEIGHTS.user_count * math.log1p(1)
        + WEIGHTS.external
        + WEIGHTS.documented
    )
    assert found == pytest.approx(expected)


def test_score_rewards_a_matching_signature_and_punishes_a_helper() -> None:
    signature = Signature([Counter({"List": 1})], Counter({"List": 1}))
    query = Query("List -> List", np.zeros(1, dtype=np.float32), set(), signature)
    plain = score(query, candidate(reached=[], users=1), 0.5, {}, 0.0)
    helper = score(query, candidate(reached=[], users=1, helper=True), 0.5, {}, 0.0)
    assert plain - helper == pytest.approx(-WEIGHTS.helper)
