import argparse
import sys
from collections import Counter

from jendhub.bench.benchmark import Benchmark
from jendhub.bench.evaluate import compare, evaluate, format_comparisons, format_scores
from jendhub.bench.judge import Judge, Pair
from jendhub.bench.judgments import JUDGED_GRADES
from jendhub.bench.pooling import (
    agreement_rows,
    answer_pairs,
    format_agreement,
    pool,
    random_negatives,
)
from jendhub.bench.queries import summary
from jendhub.lab.learned import train_all
from jendhub.lab.pipelines import DEPLOYED
from jendhub.search.ranking import MODEL

DEFAULT_DEPTH = 10
DEFAULT_K = 10


def describe_messages(judge: Judge, pairs: list[Pair]) -> str:
    lengths = [judge.message_length(judged) for judged in pairs]
    return (
        f"pairs: {len(pairs)}\n"
        f"characters of all messages: {sum(lengths)}\n"
        f"characters of the longest message: {max(lengths, default=0)}\n"
    )


def check_benchmark(benchmark: Benchmark) -> None:
    grades = Counter(judgment.grade for judgment in benchmark.judgments)
    sys.stdout.write(
        f"definitions: {len(benchmark.definitions)}\n"
        f"{summary(benchmark.queries)}\n"
        f"judgments: {len(benchmark.judgments)} current, {len(benchmark.stale)} stale\n"
        "judgments by grade: "
        + ", ".join(f"{grade}: {grades[grade]}" for grade in reversed(JUDGED_GRADES))
        + "\n"
    )


async def run_pipeline(benchmark: Benchmark, name: str, ks: list[int]) -> None:
    scores = await evaluate(await benchmark.searcher(name), benchmark.assessments(), ks)
    sys.stdout.write(format_scores(scores, ks) + "\n")


async def compare_pipelines(benchmark: Benchmark, baseline: str, candidate: str, k: int) -> None:
    comparisons = await compare(
        await benchmark.searcher(baseline),
        await benchmark.searcher(candidate),
        benchmark.assessments(),
        k,
    )
    sys.stdout.write(f"A: {baseline}\nB: {candidate}\n{format_comparisons(comparisons, k)}\n")


async def pool_pipelines(benchmark: Benchmark, names: list[str], depth: int) -> None:
    searchers = [await benchmark.searcher(name) for name in dict.fromkeys(names)]
    answered = {(query.text, id_) for query, id_ in answer_pairs(benchmark.queries)}
    pairs = [
        (query, id_)
        for query, id_ in await pool(searchers, benchmark.queries, depth)
        if (query.text, id_) not in answered
    ]
    await benchmark.judge_pairs(pairs)
    sys.stdout.write(f"the pool holds {len(pairs)} pairs without an original answer\n")


async def train_model(benchmark: Benchmark) -> None:
    resources = benchmark.resources
    vectors = await resources.graded_vectors()
    features = await resources.features(DEPLOYED)
    train_all(features, resources.graded, vectors).save_model(MODEL)
    sys.stdout.write(f"wrote {MODEL} from {len(resources.graded)} queries\n")


async def check_agreement(benchmark: Benchmark) -> None:
    ids = [definition.id for definition in benchmark.definitions]
    negatives = random_negatives(benchmark.queries, ids)
    await benchmark.judge_pairs(answer_pairs(benchmark.queries) + negatives)
    rows = agreement_rows(benchmark.queries, negatives, benchmark.judgments)
    sys.stdout.write(format_agreement(rows) + "\n")


async def run_bench(benchmark: Benchmark, subcommand: str, arguments: argparse.Namespace) -> None:
    match subcommand:
        case "check":
            check_benchmark(benchmark)
        case "run":
            benchmark.require_current()
            await run_pipeline(benchmark, arguments.pipeline, arguments.k or [DEFAULT_K])
        case "compare":
            benchmark.require_current()
            await compare_pipelines(benchmark, arguments.baseline, arguments.candidate, arguments.k)
        case "pool":
            await pool_pipelines(benchmark, arguments.pipelines, arguments.depth)
        case "train":
            benchmark.require_current()
            await train_model(benchmark)
        case "agreement":
            await check_agreement(benchmark)
        case "judge" if arguments.dry_run:
            sys.stdout.write(describe_messages(benchmark.judge, list(benchmark.stale.values())))
        case "judge":
            stale = [(p.query, p.definition.id) for p in benchmark.stale.values()]
            await benchmark.judge_pairs(stale)
        case _:
            raise AssertionError(subcommand)
