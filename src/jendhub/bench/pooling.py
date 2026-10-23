from collections import Counter

import numpy as np

from jendhub.bench.queries import GRADES, JUDGED_GRADES, Key, Query
from jendhub.bench.searchers import Searcher

SEED = 0
RANDOM = "random"


async def pool(
    searchers: list[Searcher], queries: list[Query], depth: int
) -> list[tuple[Query, str]]:
    texts = [query.text for query in queries]
    rankings = [await searcher.search(texts, depth) for searcher in searchers]
    pairs: list[tuple[Query, str]] = []
    for index, query in enumerate(queries):
        ids = dict.fromkeys(id_ for ranking in rankings for id_ in ranking[index])
        pairs += [(query, id_) for id_ in ids]
    return pairs


def random_negatives(queries: list[Query], ids: list[str]) -> list[tuple[Query, str]]:
    generator = np.random.default_rng(SEED)
    choices = sorted(ids)
    negatives: list[tuple[Query, str]] = []
    for query in queries:
        answers = {answer.id for answer in query.answers}
        while (id_ := choices[int(generator.integers(len(choices)))]) in answers:
            pass
        negatives.append((query, id_))
    return negatives


def answer_pairs(queries: list[Query]) -> list[tuple[Query, str]]:
    return [(query, answer.id) for query in queries for answer in query.answers]


def agreement_rows(
    queries: list[Query], negatives: list[tuple[Query, str]], grades: dict[Key, int]
) -> dict[str, Counter[int]]:
    rows: dict[str, Counter[int]] = {str(grade): Counter() for grade in reversed(GRADES)}
    rows[RANDOM] = Counter()
    for query in queries:
        for answer in query.answers:
            if (query.text, answer.id) in grades:
                rows[str(answer.grade)][grades[query.text, answer.id]] += 1
    for query, id_ in negatives:
        if (query.text, id_) in grades:
            rows[RANDOM][grades[query.text, id_]] += 1
    return rows


def format_agreement(rows: dict[str, Counter[int]]) -> str:
    header = "expected  pairs" + "".join(f"  {f'judged {g}':>8}" for g in JUDGED_GRADES)
    lines = [header]
    for label, counts in rows.items():
        total = counts.total()
        shares = [counts[grade] / total if total else 0.0 for grade in JUDGED_GRADES]
        lines.append(f"{label:<8}  {total:>5}" + "".join(f"  {share:>8.2f}" for share in shares))
    answers = [(int(label), counts) for label, counts in rows.items() if label != RANDOM]
    total = sum(counts.total() for _, counts in answers)
    if total:
        exact = sum(counts[grade] for grade, counts in answers) / total
        relevant = sum(counts.total() - counts[0] for _, counts in answers) / total
        lines.append(f"same grade: {exact:.2f}, judged relevant: {relevant:.2f}")
    return "\n".join(lines)
