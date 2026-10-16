from pathlib import Path

import pytest
import respx

from jendhub.corpus.dev import (
    Budget,
    DefinitionBudget,
    DevCorpus,
    DevError,
    PackageBudget,
    Selection,
    count_definitions,
    open_corpus,
    select,
)
from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, PackageRelease, Rejection, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from tests.hub.fixture import listing, serve_pages

HTTP_OLD = "0x" + "1" * 32
HTTP_NEW = "0x" + "2" * 32
TIME = "0x" + "3" * 32
JSON = "0x" + "4" * 32
UNNAMED = "0x" + "5" * 32
REJECTED = "0x" + "6" * 32

HOT = [
    listing(HTTP_OLD, "http", "0.1.0.0"),
    listing(UNNAMED),
    listing(REJECTED, "url", "0.1.0.0"),
    listing(HTTP_NEW, "http", "0.2.0.0"),
    listing(TIME, "time", "0.1.0.0"),
    listing(JSON, "json", "0.1.0.0"),
]


async def full_store(pool: Pool) -> PackageStore:
    full = PackageStore(pool)
    await full.store_base(Base("v2.0.36", b"base"))
    sources = {
        HTTP_OLD: b"def get() -> Nat:\n  0n\n",
        HTTP_NEW: b"def get() -> Nat:\n  1n\n",
        TIME: b"type Instant is Data:\n  Instant{}\n\nlaw later:\n  Nat\n",
        JSON: b"def parse() -> Nat:\n  0n\n",
        UNNAMED: b"def main() -> Nat:\n  0n\n",
    }
    for hash_, source in sources.items():
        await full.store_package(Package(Listing(hash_, None), {"main.bend": source}))
    await full.reject(Rejection(REJECTED, "invalid file name 'a b.bend'"))
    return full


async def select_hot(router: respx.MockRouter, pool: Pool, budget: Budget) -> Selection:
    serve_pages(router, [HOT])
    selection = await select(await full_store(pool), budget, set())
    assert selection.corpus.base_tag == "v2.0.36"
    return selection


async def selected(router: respx.MockRouter, pool: Pool, budget: Budget) -> list[str]:
    return [package.hash for package in (await select_hot(router, pool, budget)).corpus.packages]


def test_count_definitions_counts_top_level_declarations() -> None:
    source = b"# def note\ntype T is Data:\n  T{}\ndef f() -> T:\n  def_x = 1\nlaw l:\n  T\n"
    assert count_definitions({"a.bend": source, "b.bend": b"def g() -> T:\n"}) == 4


async def test_select_keeps_the_hottest_release_of_each_named_package(
    router: respx.MockRouter, pool: Pool
) -> None:
    assert await selected(router, pool, PackageBudget(10)) == [HTTP_OLD, TIME, JSON]


async def test_select_stops_at_the_package_budget(router: respx.MockRouter, pool: Pool) -> None:
    assert await selected(router, pool, PackageBudget(2)) == [HTTP_OLD, TIME]


async def test_select_skips_a_package_that_exceeds_the_definition_budget(
    router: respx.MockRouter, pool: Pool
) -> None:
    assert await selected(router, pool, DefinitionBudget(2)) == [HTTP_OLD, JSON]


async def test_select_stops_when_the_definition_budget_is_used(
    router: respx.MockRouter, pool: Pool
) -> None:
    assert await selected(router, pool, DefinitionBudget(1)) == [HTTP_OLD]


async def test_select_counts_the_definitions_of_the_selected_packages(
    router: respx.MockRouter, pool: Pool
) -> None:
    assert (await select_hot(router, pool, PackageBudget(3))).definitions == 4


async def test_select_requires_the_packages_in_the_full_corpus(
    router: respx.MockRouter, pool: Pool
) -> None:
    serve_pages(router, [[listing("0x" + "9" * 32, "fresh", "0.1.0.0")]])
    with pytest.raises(DevError):
        await select(await full_store(pool), PackageBudget(1), set())


def test_dev_corpus_round_trips_through_json() -> None:
    corpus = DevCorpus("v2.0.36", [PackageRelease(TIME, Release("time", "0.1.0.0"))])
    assert DevCorpus.from_json(corpus.to_json()) == corpus


def test_dev_corpus_refuses_an_unnamed_package() -> None:
    with pytest.raises(DevError):
        DevCorpus.from_json(
            f'{{"base": "v2.0.36", "packages": [{{"hash": "{TIME}", "name": null}}]}}'
        )


async def test_open_corpus_copies_the_packages_and_the_base(pool: Pool, tmp_path: Path) -> None:
    store = await full_store(pool)
    listed = DevCorpus("v2.0.36", [PackageRelease(TIME, Release("time", "0.1.0.0"))])
    corpus = await open_corpus(Library(tmp_path), store, listed)
    assert corpus.packages == listed.packages
    assert corpus.library.read_package(TIME) == (await store.package(TIME)).files
    assert corpus.library.base_source("v2.0.36").read_bytes() == b"base"


async def test_open_corpus_requires_the_packages_in_the_store(pool: Pool, tmp_path: Path) -> None:
    listed = DevCorpus("v2.0.36", [PackageRelease("0x" + "9" * 32, Release("x", "0.1.0.0"))])
    with pytest.raises(DevError):
        await open_corpus(Library(tmp_path), await full_store(pool), listed)


def test_top_keeps_the_hottest_packages() -> None:
    packages = [
        PackageRelease(TIME, Release("time", "0.1.0.0")),
        PackageRelease(JSON, Release("json", "0.1.0.0")),
    ]
    corpus = DevCorpus("v2.0.36", packages)
    assert corpus.top(1) == DevCorpus("v2.0.36", packages[:1])
    assert corpus.top(0) == DevCorpus("v2.0.36", [])


@pytest.mark.parametrize("count", [-1, 2])
def test_top_refuses_a_count_outside_the_corpus(count: int) -> None:
    with pytest.raises(DevError):
        DevCorpus("v2.0.36", [PackageRelease(TIME, Release("time", "0.1.0.0"))]).top(count)


async def test_select_skips_an_invalid_release(router: respx.MockRouter, pool: Pool) -> None:
    serve_pages(router, [HOT])
    selection = await select(await full_store(pool), PackageBudget(10), {TIME})
    assert [package.hash for package in selection.corpus.packages] == [HTTP_OLD, JSON]
