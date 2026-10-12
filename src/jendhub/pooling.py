from collections import Counter

import numpy as np

from jendhub.bench import GRADES, Query
from jendhub.evaluate import Pipeline
from jendhub.judgments import JUDGED_GRADES, Judgment

SEED = 0
RANDOM = "random"

type Key = tuple[str, str]


def pool(pipelines: list[Pipeline], queries: list[Query], depth: int) -> list[tuple[Query, str]]:
    texts = [query.query for query in queries]
    rankings = [pipeline.search(texts, depth) for pipeline in pipelines]
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
    queries: list[Query], negatives: list[tuple[Query, str]], judgments: list[Judgment]
) -> dict[str, Counter[int]]:
    grades: dict[Key, int] = {(j.query, j.id): j.grade for j in judgments}
    rows: dict[str, Counter[int]] = {str(grade): Counter() for grade in reversed(GRADES)}
    rows[RANDOM] = Counter()
    for query in queries:
        for answer in query.answers:
            if (query.query, answer.id) in grades:
                rows[str(answer.grade)][grades[query.query, answer.id]] += 1
    for query, id_ in negatives:
        if (query.query, id_) in grades:
            rows[RANDOM][grades[query.query, id_]] += 1
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
