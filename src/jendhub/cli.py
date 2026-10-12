import argparse
import asyncio
import logging
import sys
from pathlib import Path

from jendhub.dev import (
    Budget,
    DefinitionBudget,
    DevCorpus,
    DevError,
    PackageBudget,
    build,
    select,
)
from jendhub.errors import JendHubError
from jendhub.mirror import Mirror
from jendhub.sync import describe_plan, plan, sync

DEV_CORPUS_LIST = Path("bench/dev.json")


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
    dev_commands = dev_parser.add_subparsers(dest="dev_command", required=True)
    select_parser = dev_commands.add_parser(
        "select", help=f"select the hottest release of each named package into {DEV_CORPUS_LIST}"
    )
    budget = select_parser.add_mutually_exclusive_group(required=True)
    budget.add_argument("--packages", type=int, help="the number of packages")
    budget.add_argument(
        "--definitions",
        type=int,
        help="the minimum number of top-level def, type and law declarations",
    )
    dev_commands.add_parser("build", help=f"build the dev corpus from {DEV_CORPUS_LIST}")
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
    match arguments.command, getattr(arguments, "dev_command", None):
        case "sync", _ if arguments.dry_run:
            sys.stdout.write(describe_plan(asyncio.run(plan(full))) + "\n")
        case "sync", _:
            asyncio.run(sync(full))
        case "dev", "select":
            select_dev_corpus(full, budget_from(arguments))
        case "dev", "build":
            build_dev_corpus(full, arguments.data / "dev")
        case command:
            raise AssertionError(command)
