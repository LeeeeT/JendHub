import argparse
import sys
from pathlib import Path

from jendhub.corpus.corpus import Corpus, corpus_text
from jendhub.corpus.dev import (
    DEV_CORPUS_LIST,
    Budget,
    DefinitionBudget,
    PackageBudget,
    select,
)
from jendhub.hub.store import PackageStore
from jendhub.hub.sync import describe_plan, plan, sync
from jendhub.indexing.store import IndexStore


def budget_from(arguments: argparse.Namespace) -> Budget:
    if arguments.packages is not None:
        return PackageBudget(arguments.packages)
    return DefinitionBudget(arguments.definitions)


async def sync_store(store: PackageStore, *, dry_run: bool) -> None:
    if dry_run:
        sys.stdout.write(describe_plan(await plan(store)) + "\n")
        return
    found = await sync(store)
    sys.stdout.write(f"stored {len(found.entries)} new packages\n")


async def select_dev_corpus(store: PackageStore, index: IndexStore, budget: Budget) -> None:
    selection = await select(store, budget, await index.invalid_hashes())
    DEV_CORPUS_LIST.parent.mkdir(parents=True, exist_ok=True)
    DEV_CORPUS_LIST.write_text(selection.corpus.to_json())
    sys.stdout.write(
        f"selected {len(selection.corpus.packages)} packages"
        f" with {selection.definitions} definitions into {DEV_CORPUS_LIST}\n"
    )


def write_corpus_text(corpus: Corpus, output: Path) -> None:
    text = corpus_text(corpus)
    output.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {output}: {len(text)} characters\n")
