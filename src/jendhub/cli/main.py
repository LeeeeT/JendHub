import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

from jendhub.bench.benchmark import LIBRARY, QUERIES, open_benchmark, open_services, pipeline_names
from jendhub.cli.bench import DEFAULT_DEPTH, DEFAULT_K, run_bench
from jendhub.cli.corpus import (
    budget_from,
    select_dev_corpus,
    show_uses,
    sync_store,
    write_corpus_text,
)
from jendhub.cli.enrich import enrich_corpus
from jendhub.cli.index import show_status
from jendhub.corpus.corpus import Corpus
from jendhub.corpus.dev import DEV_CORPUS_LIST, dev_corpus_list, open_corpus
from jendhub.db.migrate import migrate
from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.cache import EmbeddingCache
from jendhub.enrich.store import EnrichmentStore
from jendhub.errors import JendHubError
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from jendhub.indexing.project import restore_vectors
from jendhub.indexing.schedule import clean_retired, schedule_live
from jendhub.indexing.store import IndexStore
from jendhub.logs import configure_logging
from jendhub.settings import DatabaseSettings, QdrantSettings, load_settings
from jendhub.vectors.index import open_vector_index


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
    commands.add_parser("migrate", help="apply the database migrations")
    sync_parser = commands.add_parser(
        "sync", help="store the new packages of BendHub and the latest released base.bend"
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
    commands.add_parser(
        "enrich", help="write search metadata for the definitions of the dev corpus"
    )
    uses_parser = commands.add_parser(
        "uses",
        help="show the definitions of the live index that a definition uses, and its users",
    )
    uses_parser.add_argument("id", help="a definition ID")
    index_parser = commands.add_parser("index", help="manage the search index")
    index_commands = index_parser.add_subparsers(dest="subcommand", required=True)
    index_commands.add_parser("status", help="show the generations and their jobs")
    index_commands.add_parser(
        "schedule", help="queue the jobs of the live index and promote a finished generation"
    )
    index_commands.add_parser(
        "restore-vectors",
        help="write each Qdrant collection that Postgres lists and Qdrant lacks,"
        " from the embedding cache",
    )
    retry_parser = index_commands.add_parser("retry", help="queue the failed jobs again")
    retry_parser.add_argument("generation", type=int)
    bench_parser = commands.add_parser("bench", help="manage the benchmark")
    bench_commands = bench_parser.add_subparsers(dest="subcommand", required=True)
    bench_commands.add_parser("corpus", help="write the dev corpus as one text file")
    bench_commands.add_parser("check", help=f"check {QUERIES} against the dev corpus")
    run_parser = bench_commands.add_parser("run", help="score a search pipeline on the dev corpus")
    run_parser.add_argument("pipeline", choices=pipeline_names())
    run_parser.add_argument(
        "--k",
        type=int,
        action="append",
        help=f"the cutoff of nDCG@k; repeat it for more cutoffs (default: {DEFAULT_K})",
    )
    compare_parser = bench_commands.add_parser(
        "compare", help="compare two search pipelines query by query on the dev corpus"
    )
    compare_parser.add_argument("baseline", choices=pipeline_names(), help="pipeline A")
    compare_parser.add_argument("candidate", choices=pipeline_names(), help="pipeline B")
    compare_parser.add_argument(
        "--k", type=int, default=DEFAULT_K, help="the cutoff of nDCG@k (default: %(default)s)"
    )
    pool_parser = bench_commands.add_parser("pool", help="judge the top results of pipelines")
    pool_parser.add_argument(
        "--pipelines",
        nargs="+",
        required=True,
        choices=pipeline_names(),
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


def main() -> None:
    arguments = parse_arguments()
    try:
        configure_logging(structured=False)
        asyncio.run(run(arguments))
    except JendHubError as error:
        sys.stderr.write(f"jendhub: error: {error}\n")
        raise SystemExit(1) from error
    except ValidationError as error:
        sys.stderr.write(f"jendhub: error: invalid settings: {error}\n")
        raise SystemExit(1) from error


async def run(arguments: argparse.Namespace) -> None:
    match arguments.command:
        case "migrate" | "sync" | "dev" | "uses":
            async with open_pool(load_settings(DatabaseSettings)) as pool:
                await run_store_command(pool, arguments)
        case "index":
            await run_index_command(arguments)
        case "enrich":
            async with open_services() as services:
                corpus = await selected_corpus(services.pool, arguments)
                await enrich_corpus(corpus, EnrichmentStore(services.pool), services.openrouter)
        case "bench" if arguments.subcommand == "corpus":
            async with open_pool(load_settings(DatabaseSettings)) as pool:
                corpus = await selected_corpus(pool, arguments)
                write_corpus_text(corpus, arguments.data / "dev-corpus.txt")
        case "bench":
            async with open_benchmark(arguments.data, arguments.top) as benchmark:
                await run_bench(benchmark, arguments.subcommand, arguments)
        case command:
            raise AssertionError(command)


async def selected_corpus(pool: Pool, arguments: argparse.Namespace) -> Corpus:
    listed = dev_corpus_list()
    selected = listed if arguments.top is None else listed.top(arguments.top)
    return await open_corpus(Library(arguments.data / LIBRARY), PackageStore(pool), selected)


async def run_store_command(pool: Pool, arguments: argparse.Namespace) -> None:
    match arguments.command:
        case "migrate":
            async with pool.connection() as connection:
                for name in await migrate(connection):
                    sys.stdout.write(f"applied {name}\n")
        case "sync":
            await sync_store(PackageStore(pool), dry_run=arguments.dry_run)
        case "dev":
            await select_dev_corpus(PackageStore(pool), IndexStore(pool), budget_from(arguments))
        case "uses":
            await show_uses(pool, arguments.id)
        case command:
            raise AssertionError(command)


async def run_index_command(arguments: argparse.Namespace) -> None:
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
    ):
        index = IndexStore(pool)
        match arguments.subcommand:
            case "status":
                await show_status(pool)
            case "schedule":
                await schedule_live(index, PackageStore(pool), vectors)
                await clean_retired(index, vectors)
            case "restore-vectors":
                for name in await restore_vectors(index, vectors, EmbeddingCache(pool)):
                    sys.stdout.write(f"restored {name}\n")
            case "retry":
                await index.retry_failed(arguments.generation)
            case subcommand:
                raise AssertionError(subcommand)
