import argparse
import sys
from pathlib import Path
from typing import LiteralString

from psycopg import AsyncConnection

from jendhub.corpus.corpus import Corpus, corpus_text
from jendhub.corpus.dev import (
    DEV_CORPUS_LIST,
    Budget,
    DefinitionBudget,
    PackageBudget,
    select,
)
from jendhub.db.pool import Pool
from jendhub.hub.store import PackageStore
from jendhub.hub.sync import describe_plan, plan, sync
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.uses.resolve import UsesError


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


USED_BY: LiteralString = (
    "SELECT u.id FROM uses JOIN definitions u ON u.point = uses.user_point"
    " WHERE uses.target_point = %s ORDER BY u.id"
)
USES: LiteralString = (
    "SELECT t.id FROM uses JOIN definitions t ON t.point = uses.target_point"
    " WHERE uses.user_point = %s ORDER BY t.id"
)


async def write_ids(
    connection: AsyncConnection, label: str, query: LiteralString, point: int
) -> None:
    cursor = await connection.execute(query, (point,))
    ids = [found for (found,) in await cursor.fetchall()]
    sys.stdout.write(f"{label} {len(ids)}:\n" + "".join(f"  {other}\n" for other in ids))


async def show_uses(pool: Pool, id_: str) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT d.point FROM definitions d JOIN generations g ON g.id = d.generation"
            " WHERE g.corpus = %s AND g.state = 'live' AND d.id = %s",
            (LIVE, id_),
        )
        row = await cursor.fetchone()
        if row is None:
            raise UsesError(f"{id_} is not in the live index")
        await write_ids(connection, "used by", USED_BY, row[0])
        await write_ids(connection, "uses", USES, row[0])


def write_corpus_text(corpus: Corpus, output: Path) -> None:
    text = corpus_text(corpus)
    output.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {output}: {len(text)} characters\n")
