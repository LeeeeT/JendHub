import argparse
import asyncio
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jendhub.bench import BenchError, Query, check, corpus_text, queries_within, summary
from jendhub.definitions import Corpus, corpus_definitions, source_files
from jendhub.dev import (
    Budget,
    DefinitionBudget,
    DevCorpus,
    DevError,
    PackageBudget,
    build,
    open_corpus,
    select,
)
from jendhub.embeddings import OpenRouterEmbedder
from jendhub.enrich import Window, enrich, windows
from jendhub.enrichments import EnrichError, EnrichmentStore
from jendhub.errors import JendHubError
from jendhub.evaluate import Pipeline, compare, evaluate, format_comparisons, format_scores
from jendhub.judge import Judge, Pair, current_judgments, pair
from jendhub.judgments import JUDGED_GRADES, Assessment, Judgment, JudgmentStore, assessments
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
from jendhub.uses import UsesError, use_graph

DEV_CORPUS_LIST = Path("bench/dev.json")
QUERIES = Path("bench/queries.jsonl")
JUDGMENTS = Path("judgments.sqlite")
JUDGE_PROMPT = Path("bench/JUDGE.md")
DEFAULT_DEPTH = 10
EMBEDDINGS = Path("embeddings.sqlite")
ENRICHMENTS = Path("enrichments.sqlite")
DEFAULT_K = 10


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jendhub")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--top",
        type=int,
        metavar="N",
        help="use only Base and the N hottest packages of the dev corpus",
    )
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
    uses_parser = commands.add_parser(
        "uses", help="show the definitions that a definition uses, and the ones that use it"
    )
    uses_parser.add_argument("id", help="a definition ID")
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
    pool_parser = bench_commands.add_parser("pool", help="judge the top results of pipelines")
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
    judge_parser = bench_commands.add_parser(
        "judge", help="judge again each pair with a stale grade"
    )
    judge_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the size of the messages to send, and send nothing",
    )
    arguments = parser.parse_args()
    if arguments.top is not None and arguments.command not in ("enrich", "bench"):
        parser.error("--top applies only to enrich and bench")
    return arguments


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


def dev_corpus_list() -> DevCorpus:
    if not DEV_CORPUS_LIST.is_file():
        raise DevError(f"{DEV_CORPUS_LIST} does not exist: run jendhub dev select first")
    return DevCorpus.from_json(DEV_CORPUS_LIST.read_text())


def build_dev_corpus(full: Mirror, dev_root: Path) -> None:
    corpus = dev_corpus_list()
    asyncio.run(build(full, dev_root, corpus))
    sys.stdout.write(f"built {dev_root} with {len(corpus.packages)} packages\n")


def dev_corpus(data: Path, top: int | None) -> Corpus:
    listed = dev_corpus_list()
    return open_corpus(Mirror(data / "dev"), listed if top is None else listed.top(top))


def show_uses(corpus: Corpus, full: Mirror, id_: str) -> None:
    definitions = corpus_definitions(corpus)
    if id_ not in {definition.id for definition in definitions}:
        raise UsesError(f"{id_} is not in the dev corpus")
    graph = use_graph(corpus, full, definitions)
    for label, error in sorted(graph.errors.items()):
        sys.stdout.write(f"{label} does not load: {error}\n")
    for label, found in (("used by", graph.users), ("uses", graph.used)):
        ids = sorted(found.get(id_, frozenset()))
        sys.stdout.write(f"{label} {len(ids)}:\n" + "".join(f"  {other}\n" for other in ids))


def write_corpus_text(corpus: Corpus, output: Path) -> None:
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
    judge: Judge
    store: JudgmentStore
    judgments: list[Judgment]
    stale: dict[tuple[str, str], Pair]

    def assessments(self) -> list[Assessment]:
        return assessments(self.queries, self.judgments)

    def pipeline(self, name: str) -> Pipeline:
        return PIPELINES[name](self.resources)

    def report_load_errors(self) -> None:
        for label, error in sorted(self.resources.graph.errors.items()):
            sys.stdout.write(f"{label} does not load, so the judge sees no uses: {error}\n")

    def judge_pairs(self, pairs: list[tuple[Query, str]]) -> None:
        known = {(judgment.query, judgment.id) for judgment in self.judgments}
        definitions = {definition.id: definition for definition in self.resources.definitions}
        unique = {(query.query, id_): (query, id_) for query, id_ in pairs}
        new = [
            pair(query, definitions[id_], self.resources.graph, definitions)
            for key, (query, id_) in unique.items()
            if key not in known
        ]
        if new:
            self.report_load_errors()

        def record(judged: Pair, grade: int) -> None:
            judgment = Judgment(judged.query.query, judged.definition.id, grade)
            self.store.store(self.judge.key(judged), judgment)
            self.judgments.append(judgment)
            self.stale.pop((judgment.query, judgment.id), None)

        self.judge.judge(new, record)

    def require_current(self) -> None:
        if self.stale:
            raise BenchError(
                f"{len(self.stale)} judged pairs have a stale grade: run jendhub bench judge"
            )


def corpus_windows(corpus: Corpus) -> list[Window]:
    return [window for file in source_files(corpus) for window in windows(file)]


def enrich_corpus(corpus: Corpus, store: EnrichmentStore) -> None:
    stored = store.load(corpus_windows(corpus))
    sys.stdout.write(
        f"{len(stored.enrichments)} definitions have enrichments;"
        f" {len(stored.rejected)} windows are rejected; {len(stored.missing)} windows remain\n"
    )
    outcome = enrich(stored.missing, store.store, store.reject)
    for rejection in outcome.rejected:
        sys.stdout.write(f"rejected: {rejection}\n")
    for error in outcome.errors:
        sys.stdout.write(f"failed: {error}\n")
    if outcome.errors:
        raise EnrichError(f"{len(outcome.errors)} requests failed: run jendhub enrich again")


def load_benchmark(data: Path, top: int | None) -> Benchmark:
    if not QUERIES.is_file():
        raise BenchError(f"{QUERIES} does not exist")
    corpus = dev_corpus(data, top)
    definitions = corpus_definitions(corpus)
    enrichments = EnrichmentStore(data / ENRICHMENTS).load(corpus_windows(corpus)).enrichments
    graph = use_graph(corpus, Mirror(data / "full"), definitions)
    resources = Resources(definitions, OpenRouterEmbedder(data / EMBEDDINGS), enrichments, graph)
    ids = {definition.id for definition in definitions}
    known = ids if top is None else {d.id for d in corpus_definitions(dev_corpus(data, None))}
    queries, problems = check(QUERIES.read_text(encoding="utf-8"), known)
    report_problems(QUERIES, problems)
    queries = queries_within(queries, ids)
    judge = Judge(JUDGE_PROMPT.read_text(encoding="utf-8"))
    store = JudgmentStore(data / JUDGMENTS)
    judgments, stale = current_judgments(store.load(), queries, definitions, judge, graph)
    return Benchmark(resources, queries, judge, store, judgments, stale)


def describe_messages(judge: Judge, pairs: list[Pair]) -> str:
    lengths = [judge.message_length(judged) for judged in pairs]
    return (
        f"pairs: {len(pairs)}\n"
        f"characters of all messages: {sum(lengths)}\n"
        f"characters of the longest message: {max(lengths, default=0)}\n"
    )


def check_benchmark(benchmark: Benchmark) -> None:
    benchmark.report_load_errors()
    grades = Counter(judgment.grade for judgment in benchmark.judgments)
    sys.stdout.write(
        f"definitions: {len(benchmark.resources.definitions)}\n"
        f"{summary(benchmark.queries)}\n"
        f"judgments: {len(benchmark.judgments)} current, {len(benchmark.stale)} stale\n"
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
                dev_corpus(arguments.data, arguments.top),
                EnrichmentStore(arguments.data / ENRICHMENTS),
            )
        case "uses", _:
            show_uses(dev_corpus(arguments.data, None), full, arguments.id)
        case "bench", "corpus":
            write_corpus_text(
                dev_corpus(arguments.data, arguments.top), arguments.data / "dev-corpus.txt"
            )
        case "bench", str() as subcommand:
            run_bench(load_benchmark(arguments.data, arguments.top), subcommand, arguments)
        case command:
            raise AssertionError(command)


def run_bench(benchmark: Benchmark, subcommand: str, arguments: argparse.Namespace) -> None:
    match subcommand:
        case "check":
            check_benchmark(benchmark)
        case "run":
            benchmark.require_current()
            run_pipeline(benchmark, arguments.pipeline, arguments.k or [DEFAULT_K])
        case "compare":
            benchmark.require_current()
            compare_pipelines(benchmark, arguments.baseline, arguments.candidate, arguments.k)
        case "pool":
            pool_pipelines(benchmark, arguments.pipelines, arguments.depth)
        case "agreement":
            check_agreement(benchmark)
        case "judge" if arguments.dry_run:
            sys.stdout.write(describe_messages(benchmark.judge, list(benchmark.stale.values())))
        case "judge":
            benchmark.judge_pairs([(p.query, p.definition.id) for p in benchmark.stale.values()])
        case _:
            raise AssertionError(subcommand)
