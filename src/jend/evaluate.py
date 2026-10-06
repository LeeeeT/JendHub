import argparse
import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

from pydantic import BaseModel, ConfigDict

from jend.benchmark import EXACT, Query, auc, bootstrap, load, ndcg, recall, reciprocal_rank
from jend.features import Featurizer, positions
from jend.index import load as load_index
from jend.ranker import example, fit, folds, query_vectors
from jend.search import order

CUTOFF = 10
RESULTS = 20
DEPTHS = (10, 25, 50, 100)
COMPARED_DEPTH = 50
SHOWN = 10


class QueryRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    stages: dict[str, tuple[str, ...]]
    scores: tuple[float, ...]
    seconds: float

    @property
    def ranking(self) -> tuple[str, ...]:
        return self.stages["ranking"]


class Run(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    created: datetime
    index: str
    queries: tuple[QueryRun, ...]


def execute(data: Path, queries: Sequence[Query], label: str) -> Run:
    index = load_index(data)
    featurizer = Featurizer(index)
    vectors = asyncio.run(query_vectors(data, queries))
    started = time.monotonic()
    candidates = [
        featurizer.candidates(query.query, vector)
        for query, vector in zip(queries, vectors, strict=True)
    ]
    seconds = (time.monotonic() - started) / len(queries)
    examples = [example(index, query, found) for query, found in zip(queries, candidates)]
    runs: dict[int, QueryRun] = {}
    for fold in folds(len(queries)):
        held_out = set(fold)
        ranker = fit([e for position, e in enumerate(examples) if position not in held_out])
        for position in fold:
            found = candidates[position]
            rows, probabilities = order(ranker, featurizer, found)
            stages = {
                "bm25": found.lexical,
                "vector": found.semantic,
                "ranking": rows,
            }
            runs[position] = QueryRun(
                id=queries[position].id,
                stages={
                    name: tuple(index.documents[row].key for row in positions(rows))
                    for name, rows in stages.items()
                },
                scores=tuple(float(probability) for probability in probabilities[:RESULTS]),
                seconds=seconds,
            )
    return Run(
        label=label,
        created=datetime.now(UTC),
        index=index.id,
        queries=tuple(runs[position] for position in range(len(queries))),
    )


def measures(query: Query, run: QueryRun) -> dict[str, float]:
    answers = query.answers()
    values = {
        f"{stage} recall@{depth}": recall(keys, answers, depth)
        for stage, keys in run.stages.items()
        for depth in DEPTHS
    }
    grades = query.grades()
    top = run.ranking[0] if run.ranking else None
    values[f"nDCG@{CUTOFF}"] = ndcg(run.ranking, grades, CUTOFF)
    values["top 1 exact"] = float(top is not None and grades.get(top) == EXACT)
    values["MRR"] = reciprocal_rank(run.ranking, answers)
    values[f"recall@{RESULTS}"] = recall(run.ranking, answers, RESULTS)
    return values


def headline(run: Run) -> list[str]:
    stages = list(run.queries[0].stages)
    ranked = [f"nDCG@{CUTOFF}", "top 1 exact", "MRR", f"recall@{RESULTS}"]
    return ranked + [f"{stage} recall@{COMPARED_DEPTH}" for stage in stages]


def _pair(queries: Sequence[Query], run: Run) -> list[tuple[Query, QueryRun]]:
    by_id = {query.id: query for query in run.queries}
    missing = [query.id for query in queries if query.id not in by_id]
    if missing:
        raise SystemExit(f"run {run.label!r} has no result for {', '.join(missing)}")
    return [(query, by_id[query.id]) for query in queries]


def _label(query: Query, key: str) -> str:
    judgment = next((j for j in query.judgments if j.key == key), None)
    if judgment is None:
        return f"unjudged {key}"
    return f"{judgment.definition} (grade {judgment.grade})"


def report(queries: Sequence[Query], run: Run) -> None:
    pairs = _pair(queries, run)
    answered = [(query, result) for query, result in pairs if query.answers()]
    unanswered = [(query, result) for query, result in pairs if not query.answers()]
    judgments = sum(len(query.judgments) for query in queries)
    print(
        f"benchmark: {len(queries)} queries, {len(answered)} with an answer,"
        f" {len(unanswered)} without, {judgments} judgments"
    )
    seconds = [result.seconds for _, result in pairs]
    print(
        f"run {run.label!r}: {run.created:%Y-%m-%d %H:%M} UTC, index {run.index},"
        f" ranking time {median(seconds):.3f} s for each query"
    )
    values = [measures(query, result) for query, result in answered]
    print(f"\nranking, {len(answered)} queries with an answer (grade 2 or 3)")
    for name in headline(run)[:4]:
        print(f"  {name:12} {mean(value[name] for value in values):.3f}")
    stages = list(pairs[0][1].stages)
    print(
        f"\nshare of answers found, by stage and depth\n  {'':12}"
        + "".join(f"{f'@{depth}':>8}" for depth in DEPTHS)
        + f"{'any':>8}"
    )
    for stage in stages:
        row = "".join(
            f"{mean(value[f'{stage} recall@{depth}'] for value in values):8.3f}" for depth in DEPTHS
        )
        found = sum(bool(query.answers() & set(result.stages[stage])) for query, result in answered)
        print(f"  {stage:12}{row}{f'{found}/{len(answered)}':>8}")
    _detection(answered, unanswered)
    _styles(answered, values)
    _worst(answered, values)
    _unjudged(pairs)


def _detection(
    answered: Sequence[tuple[Query, QueryRun]], unanswered: Sequence[tuple[Query, QueryRun]]
) -> None:
    positives = [result.scores[0] for _, result in answered if result.scores]
    negatives = [result.scores[0] for _, result in unanswered if result.scores]
    if not negatives:
        return
    print(
        f"\nanswer detection by the best score: AUC {auc(positives, negatives):.3f},"
        f" best score without an answer: max {max(negatives):.2f}, median {median(negatives):.2f};"
        f" with an answer: median {median(positives):.2f}, min {min(positives):.2f}"
    )


def _styles(answered: Sequence[tuple[Query, QueryRun]], values: list[dict[str, float]]) -> None:
    groups: defaultdict[str, list[float]] = defaultdict(list)
    for (query, _), value in zip(answered, values):
        groups[query.style].append(value[f"nDCG@{CUTOFF}"])
    print(f"\nnDCG@{CUTOFF} by query style")
    for style, scores in sorted(groups.items(), key=lambda item: -len(item[1])):
        print(f"  {style:12} {mean(scores):.3f}  ({len(scores)} queries)")


def _worst(answered: Sequence[tuple[Query, QueryRun]], values: list[dict[str, float]]) -> None:
    ordered = sorted(zip(answered, values), key=lambda item: item[1][f"nDCG@{CUTOFF}"])
    print(f"\nlowest nDCG@{CUTOFF}")
    for (query, result), value in ordered[:SHOWN]:
        ranking = result.ranking
        first = next((rank for rank, key in enumerate(ranking, 1) if key in query.answers()), None)
        top = _label(query, ranking[0]) if ranking else "nothing"
        print(
            f"  {query.id} {value[f'nDCG@{CUTOFF}']:.2f} {query.query!r}\n"
            f"       first answer at {first or 'none'}, top: {top}"
        )


def _unjudged(pairs: Sequence[tuple[Query, QueryRun]]) -> None:
    missing = [
        (query.id, rank, key)
        for query, result in pairs
        for rank, key in enumerate(result.ranking[:CUTOFF], 1)
        if key not in query.grades()
    ]
    shown = sum(min(CUTOFF, len(result.ranking)) for _, result in pairs)
    print(f"\nunjudged in the top {CUTOFF}: {len(missing)} of {shown}")
    for query_id, rank, key in missing[:SHOWN]:
        print(f"  {query_id} rank {rank}: {key}")


def compare(queries: Sequence[Query], before: Run, after: Run) -> None:
    answered = [query for query in queries if query.answers()]
    old = {query.id: measures(query, result) for query, result in _pair(answered, before)}
    new = {query.id: measures(query, result) for query, result in _pair(answered, after)}
    names = [name for name in headline(after) if name in headline(before)]
    print(f"{before.label!r} -> {after.label!r}, {len(answered)} queries with an answer")
    print(f"  {'':24}{'before':>8}{'after':>8}{'delta':>8}   95% interval   better worse")
    for name in names:
        deltas = [new[query.id][name] - old[query.id][name] for query in answered]
        low, high = bootstrap(deltas)
        better = sum(delta > 0 for delta in deltas)
        worse = sum(delta < 0 for delta in deltas)
        print(
            f"  {name:24}{mean(old[q.id][name] for q in answered):8.3f}"
            f"{mean(new[q.id][name] for q in answered):8.3f}{mean(deltas):+8.3f}"
            f"   {low:+.3f} {high:+.3f}{better:8}{worse:6}"
        )
    main_name = names[0]
    changes = sorted(answered, key=lambda q: -abs(new[q.id][main_name] - old[q.id][main_name]))
    print(f"\nlargest changes of {main_name}")
    for query in changes[:SHOWN]:
        before_value, after_value = old[query.id][main_name], new[query.id][main_name]
        if before_value != after_value:
            print(f"  {query.id} {before_value:.2f} -> {after_value:.2f} {query.query!r}")


def _read(path: Path) -> Run:
    return Run.model_validate_json(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the search on the benchmark.")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/benchmark.json"))
    parser.add_argument("--label", default="run", help="name of the run, part of its file name")
    parser.add_argument("--against", type=Path, help="compare the new run with this run")
    parser.add_argument("--report", type=Path, help="score a saved run again, do not search")
    parser.add_argument("--compare", type=Path, nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    queries = load(args.benchmark)
    if args.compare is not None:
        compare(queries, _read(args.compare[0]), _read(args.compare[1]))
        return
    if args.report is not None:
        report(queries, _read(args.report))
        return
    run = execute(args.data, queries, args.label)
    stamp = run.created.strftime("%Y%m%d-%H%M%S")
    target = args.data / "runs" / f"{stamp}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', run.label)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(run.model_dump_json(), encoding="utf-8")
    report(queries, run)
    print(f"\nsaved {target}")
    if args.against is not None:
        print()
        compare(queries, _read(args.against), run)


if __name__ == "__main__":
    main()
