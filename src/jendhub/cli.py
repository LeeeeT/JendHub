import argparse
import asyncio
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jendhub.bench import BenchError, Query, check, corpus_text, summary
from jendhub.definitions import corpus_definitions, source_files
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
from jendhub.enrich import Window, enrich, windows
from jendhub.enrichments import EnrichError, EnrichmentStore
from jendhub.errors import JendHubError
from jendhub.evaluate import Pipeline, compare, evaluate, format_comparisons, format_scores
from jendhub.judge import Judge, Pair
from jendhub.judgments import (
    JUDGED_GRADES,
    Assessment,
    Judgment,
    assessments,
    check_judgments,
    judgment_line,
)
from jendhub.mirror import Mirror
from jendhub.pipelines import PIPELINES, Resources
from jendhub.pooling import (
    agreement_rows,
    answer_pairs,
    format_agreement,
    pool,
    random_negatives,
)
from jendhub.sync import describe_plan, plan, sync

DEV_CORPUS_LIST = Path("bench/dev.json")
QUERIES = Path("bench/queries.jsonl")
JUDGMENTS = Path("bench/judgments.jsonl")
JUDGE_PROMPT = Path("bench/JUDGE.md")
DEFAULT_DEPTH = 10
EMBEDDINGS = Path("embeddings.sqlite")
ENRICHMENTS = Path("enrichments.sqlite")
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
    commands.add_parser(
        "enrich", help="write search metadata for the definitions of the dev corpus"
    )
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
    pool_parser = bench_commands.add_parser(
        "pool", help=f"judge the top results of pipelines into {JUDGMENTS}"
    )
    pool_parser.add_argument(
        "--pipelines",
        nargs="+",
        required=True,
        choices=sorted(PIPELINES),
        help="the pipelines that fill the pool",
    )
    pool_parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help="the number of top results of each pipeline (default: %(default)s)",
    )
    bench_commands.add_parser(
        "agreement",
        help="judge the original answers and random definitions, and compare the grades",
    )
    return parser.parse_args()


def budget_from(arguments: argparse.Namespace) -> Budget:
    if arguments.packages is not None:
        return PackageBudget(arguments.packages)
    return DefinitionBudget(arguments.definitions)


def select_dev_corpus(full: Mirror, budget: Budget) -> None:
    selection = asyncio.run(select(full, budget))
    DEV_CORPUS_LIST.parent.mkdir(parents=True, exist_ok=True)
    DEV_CORPUS_LIST.write_text(selection.corpus.to_json())
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
    output.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {output}: {len(text)} characters\n")


def report_problems(path: Path, problems: list[str]) -> None:
    for problem in problems:
        sys.stdout.write(f"{path}: {problem}\n")
    if problems:
        raise BenchError(f"{len(problems)} problems in {path}: run jendhub bench check")


@dataclass(frozen=True)
class Benchmark:
    resources: Resources
    queries: list[Query]
    judgments: list[Judgment]
    judge: Judge

    def assessments(self) -> list[Assessment]:
        return assessments(self.queries, self.judgments)

    def pipeline(self, name: str) -> Pipeline:
        return PIPELINES[name](self.resources)

    def judge_pairs(self, pairs: list[tuple[Query, str]]) -> None:
        known = {(judgment.query, judgment.id) for judgment in self.judgments}
        definitions = {definition.id: definition for definition in self.resources.definitions}
        unique = {(query.query, id_): (query, id_) for query, id_ in pairs}
        new = [
            Pair(query, definitions[id_])
            for key, (query, id_) in unique.items()
            if key not in known
        ]
        with JUDGMENTS.open("a", encoding="utf-8") as output:

            def record(pair: Pair, grade: int) -> None:
                judgment = Judgment(pair.query.query, pair.definition.id, grade, self.judge.name)
                self.judgments.append(judgment)
                output.write(judgment_line(judgment))
                output.flush()

            self.judge.judge(new, record)


def corpus_windows(corpus: Mirror) -> list[Window]:
    return [window for file in source_files(corpus) for window in windows(file)]


def enrich_corpus(corpus: Mirror, store: EnrichmentStore) -> None:
    require_corpus(corpus)
    found, missing = store.load(corpus_windows(corpus))
    sys.stdout.write(f"{len(found)} definitions have enrichments; {len(missing)} windows remain\n")
    outcome = enrich(missing, store.store)
    for failure in outcome.failures:
        sys.stdout.write(f"failed: {failure}\n")
    if outcome.failures:
        raise EnrichError(f"{len(outcome.failures)} windows failed: run jendhub enrich again")


def load_benchmark(corpus: Mirror, data: Path) -> Benchmark:
    require_corpus(corpus)
    if not QUERIES.is_file():
        raise BenchError(f"{QUERIES} does not exist")
    definitions = corpus_definitions(corpus)
    enrichments, _ = EnrichmentStore(data / ENRICHMENTS).load(corpus_windows(corpus))
    resources = Resources(definitions, OpenRouterEmbedder(data / EMBEDDINGS), enrichments)
    ids = {definition.id for definition in definitions}
    queries, problems = check(QUERIES.read_text(encoding="utf-8"), ids)
    report_problems(QUERIES, problems)
    judge = Judge(JUDGE_PROMPT.read_text(encoding="utf-8"))
    judgments: list[Judgment] = []
    if JUDGMENTS.is_file():
        text = JUDGMENTS.read_text(encoding="utf-8")
        judgments, problems = check_judgments(text, queries, ids, judge.name)
        report_problems(JUDGMENTS, problems)
    return Benchmark(resources, queries, judgments, judge)


def check_benchmark(benchmark: Benchmark) -> None:
    grades = Counter(judgment.grade for judgment in benchmark.judgments)
    sys.stdout.write(
        f"{summary(benchmark.queries)}\n"
        f"judgments: {len(benchmark.judgments)} by {benchmark.judge.name}\n"
        "judgments by grade: "
        + ", ".join(f"{grade}: {grades[grade]}" for grade in reversed(JUDGED_GRADES))
        + "\n"
    )


def run_pipeline(benchmark: Benchmark, name: str, ks: list[int]) -> None:
    scores = evaluate(benchmark.pipeline(name), benchmark.assessments(), ks)
    sys.stdout.write(format_scores(scores, ks) + "\n")


def compare_pipelines(benchmark: Benchmark, baseline: str, candidate: str, k: int) -> None:
    comparisons = compare(
        benchmark.pipeline(baseline), benchmark.pipeline(candidate), benchmark.assessments(), k
    )
    sys.stdout.write(f"A: {baseline}\nB: {candidate}\n{format_comparisons(comparisons, k)}\n")


def pool_pipelines(benchmark: Benchmark, names: list[str], depth: int) -> None:
    pipelines = [benchmark.pipeline(name) for name in dict.fromkeys(names)]
    answered = {(query.query, id_) for query, id_ in answer_pairs(benchmark.queries)}
    pairs = [
        (query, id_)
        for query, id_ in pool(pipelines, benchmark.queries, depth)
        if (query.query, id_) not in answered
    ]
    benchmark.judge_pairs(pairs)
    sys.stdout.write(f"the pool holds {len(pairs)} pairs without an original answer\n")


def check_agreement(benchmark: Benchmark) -> None:
    ids = [definition.id for definition in benchmark.resources.definitions]
    negatives = random_negatives(benchmark.queries, ids)
    benchmark.judge_pairs(answer_pairs(benchmark.queries) + negatives)
    rows = agreement_rows(benchmark.queries, negatives, benchmark.judgments)
    sys.stdout.write(format_agreement(rows) + "\n")


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
        case "enrich", _:
            enrich_corpus(
                Mirror(arguments.data / "dev"), EnrichmentStore(arguments.data / ENRICHMENTS)
            )
        case "bench", "corpus":
            write_corpus_text(Mirror(arguments.data / "dev"), arguments.data / "dev-corpus.txt")
        case "bench", str() as subcommand:
            run_bench(
                load_benchmark(Mirror(arguments.data / "dev"), arguments.data),
                subcommand,
                arguments,
            )
        case command:
            raise AssertionError(command)


def run_bench(benchmark: Benchmark, subcommand: str, arguments: argparse.Namespace) -> None:
    match subcommand:
        case "check":
            check_benchmark(benchmark)
        case "run":
            run_pipeline(benchmark, arguments.pipeline, arguments.k or [DEFAULT_K])
        case "compare":
            compare_pipelines(benchmark, arguments.baseline, arguments.candidate, arguments.k)
        case "pool":
            pool_pipelines(benchmark, arguments.pipelines, arguments.depth)
        case "agreement":
            check_agreement(benchmark)
        case _:
            raise AssertionError(subcommand)
