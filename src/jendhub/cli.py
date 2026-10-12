import argparse
import asyncio
import logging
import sys
from pathlib import Path

from jendhub.bench import BenchError, Query, check, corpus_text, definition_ids, summary
from jendhub.definitions import corpus_definitions
from jendhub.dev import (
    Budget,
    DefinitionBudget,
    DevCorpus,
    DevError,
    PackageBudget,
    build,
    select,
)
from jendhub.embeddings import OpenRouterEmbedder
from jendhub.errors import JendHubError
from jendhub.evaluate import compare, evaluate, format_comparisons, format_scores
from jendhub.mirror import Mirror
from jendhub.pipelines import PIPELINES, Resources
from jendhub.sync import describe_plan, plan, sync

DEV_CORPUS_LIST = Path("bench/dev.json")
QUERIES = Path("bench/queries.jsonl")
EMBEDDINGS = Path("embeddings.sqlite")
DEFAULT_K = 10


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jendhub")
    parser.add_argument("--data", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser(
        "sync", help="mirror BendHub and the latest released base.bend into the full corpus"
    )
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="print the plan and change nothing"
    )
    dev_parser = commands.add_parser("dev", help="manage the dev corpus")
    dev_commands = dev_parser.add_subparsers(dest="subcommand", required=True)
    select_parser = dev_commands.add_parser(
        "select", help=f"select the hottest release of each named package into {DEV_CORPUS_LIST}"
    )
    budget = select_parser.add_mutually_exclusive_group(required=True)
    budget.add_argument("--packages", type=int, help="the number of packages")
    budget.add_argument(
        "--definitions",
        type=int,
        help="the maximum number of top-level def, type and law declarations",
    )
    dev_commands.add_parser("build", help=f"build the dev corpus from {DEV_CORPUS_LIST}")
    bench_parser = commands.add_parser("bench", help="manage the benchmark")
    bench_commands = bench_parser.add_subparsers(dest="subcommand", required=True)
    bench_commands.add_parser("corpus", help="write the dev corpus as one text file")
    bench_commands.add_parser("check", help=f"check {QUERIES} against the dev corpus")
    run_parser = bench_commands.add_parser("run", help="score a search pipeline on the dev corpus")
    run_parser.add_argument("pipeline", choices=sorted(PIPELINES))
    run_parser.add_argument(
        "--k",
        type=int,
        action="append",
        help=f"the cutoff of nDCG@k; repeat it for more cutoffs (default: {DEFAULT_K})",
    )
    compare_parser = bench_commands.add_parser(
        "compare", help="compare two search pipelines query by query on the dev corpus"
    )
    compare_parser.add_argument("baseline", choices=sorted(PIPELINES), help="pipeline A")
    compare_parser.add_argument("candidate", choices=sorted(PIPELINES), help="pipeline B")
    compare_parser.add_argument(
        "--k", type=int, default=DEFAULT_K, help="the cutoff of nDCG@k (default: %(default)s)"
    )
    return parser.parse_args()


def budget_from(arguments: argparse.Namespace) -> Budget:
    if arguments.packages is not None:
        return PackageBudget(arguments.packages)
    return DefinitionBudget(arguments.definitions)


def select_dev_corpus(full: Mirror, budget: Budget) -> None:
    selection = asyncio.run(select(full, budget))
    DEV_CORPUS_LIST.parent.mkdir(parents=True, exist_ok=True)
    DEV_CORPUS_LIST.write_text(selection.corpus.to_json(), newline="\n")
    sys.stdout.write(
        f"selected {len(selection.corpus.packages)} packages"
        f" with {selection.definitions} definitions into {DEV_CORPUS_LIST}\n"
    )


def build_dev_corpus(full: Mirror, dev_root: Path) -> None:
    if not DEV_CORPUS_LIST.is_file():
        raise DevError(f"{DEV_CORPUS_LIST} does not exist: run jendhub dev select first")
    corpus = DevCorpus.from_json(DEV_CORPUS_LIST.read_text())
    asyncio.run(build(full, dev_root, corpus))
    sys.stdout.write(f"built {dev_root} with {len(corpus.packages)} packages\n")


def require_corpus(corpus: Mirror) -> None:
    if corpus.stored_base_tag() is None:
        raise BenchError(f"{corpus.root} has no corpus: run jendhub dev build first")


def write_corpus_text(corpus: Mirror, output: Path) -> None:
    require_corpus(corpus)
    text = corpus_text(corpus)
    output.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {output}: {len(text)} characters\n")


def checked_queries(corpus: Mirror) -> list[Query]:
    require_corpus(corpus)
    if not QUERIES.is_file():
        raise BenchError(f"{QUERIES} does not exist")
    queries, problems = check(QUERIES.read_text(encoding="utf-8"), definition_ids(corpus))
    for problem in problems:
        sys.stdout.write(f"{QUERIES}: {problem}\n")
    if problems:
        raise BenchError(f"{len(problems)} problems in {QUERIES}: run jendhub bench check")
    return queries


def check_queries(corpus: Mirror) -> None:
    sys.stdout.write(summary(checked_queries(corpus)) + "\n")


def resources_for(corpus: Mirror, embeddings: Path) -> Resources:
    return Resources(corpus_definitions(corpus), OpenRouterEmbedder(embeddings))


def run_pipeline(corpus: Mirror, embeddings: Path, name: str, ks: list[int]) -> None:
    queries = checked_queries(corpus)
    pipeline = PIPELINES[name](resources_for(corpus, embeddings))
    sys.stdout.write(format_scores(evaluate(pipeline, queries, ks), ks) + "\n")


def compare_pipelines(
    corpus: Mirror, embeddings: Path, baseline: str, candidate: str, k: int
) -> None:
    queries = checked_queries(corpus)
    resources = resources_for(corpus, embeddings)
    comparisons = compare(
        PIPELINES[baseline](resources), PIPELINES[candidate](resources), queries, k
    )
    sys.stdout.write(f"A: {baseline}\nB: {candidate}\n{format_comparisons(comparisons, k)}\n")


def main() -> None:
    arguments = parse_arguments()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run(arguments)
    except JendHubError as error:
        sys.stderr.write(f"jendhub: error: {error}\n")
        raise SystemExit(1) from error


def run(arguments: argparse.Namespace) -> None:
    full = Mirror(arguments.data / "full")
    match arguments.command, getattr(arguments, "subcommand", None):
        case "sync", _ if arguments.dry_run:
            sys.stdout.write(describe_plan(asyncio.run(plan(full))) + "\n")
        case "sync", _:
            asyncio.run(sync(full))
        case "dev", "select":
            select_dev_corpus(full, budget_from(arguments))
        case "dev", "build":
            build_dev_corpus(full, arguments.data / "dev")
        case "bench", "corpus":
            write_corpus_text(Mirror(arguments.data / "dev"), arguments.data / "dev-corpus.txt")
        case "bench", "check":
            check_queries(Mirror(arguments.data / "dev"))
        case "bench", "run":
            run_pipeline(
                Mirror(arguments.data / "dev"),
                arguments.data / EMBEDDINGS,
                arguments.pipeline,
                arguments.k or [DEFAULT_K],
            )
        case "bench", "compare":
            compare_pipelines(
                Mirror(arguments.data / "dev"),
                arguments.data / EMBEDDINGS,
                arguments.baseline,
                arguments.candidate,
                arguments.k,
            )
        case command:
            raise AssertionError(command)
