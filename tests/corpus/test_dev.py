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
from jendhub.hub.api import Listing, Package, PackageRelease, Rejection, Release
from jendhub.hub.base import Base
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


async def full_store(full: PackageStore) -> PackageStore:
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


async def select_hot(router: respx.MockRouter, packages: PackageStore, budget: Budget) -> Selection:
    serve_pages(router, [HOT])
    selection = await select(await full_store(packages), budget, set())
    assert selection.corpus.base_tag == "v2.0.36"
    return selection


async def selected(router: respx.MockRouter, packages: PackageStore, budget: Budget) -> list[str]:
    found = await select_hot(router, packages, budget)
    return [package.hash for package in found.corpus.packages]


def test_count_definitions_counts_top_level_declarations() -> None:
    source = b"# def note\ntype T is Data:\n  T{}\ndef f() -> T:\n  def_x = 1\nlaw l:\n  T\n"
    assert count_definitions({"a.bend": source, "b.bend": b"def g() -> T:\n"}) == 4


def test_count_definitions_reads_a_file_that_is_not_utf_8() -> None:
    assert count_definitions({"a.bend": b"# caf\xe9\ndef f() -> T:\n  x\n"}) == 1


async def test_select_keeps_the_hottest_release_of_each_named_package(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    assert await selected(router, packages, PackageBudget(10)) == [HTTP_OLD, TIME, JSON]


async def test_select_stops_at_the_package_budget(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    assert await selected(router, packages, PackageBudget(2)) == [HTTP_OLD, TIME]


async def test_select_skips_a_package_that_exceeds_the_definition_budget(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    assert await selected(router, packages, DefinitionBudget(2)) == [HTTP_OLD, JSON]


async def test_select_stops_when_the_definition_budget_is_used(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    assert await selected(router, packages, DefinitionBudget(1)) == [HTTP_OLD]


async def test_select_counts_the_definitions_of_the_selected_packages(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    assert (await select_hot(router, packages, PackageBudget(3))).definitions == 4


async def test_select_requires_the_packages_in_the_full_corpus(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_pages(router, [[listing("0x" + "9" * 32, "fresh", "0.1.0.0")]])
    with pytest.raises(DevError):
        await select(await full_store(packages), PackageBudget(1), set())


def test_dev_corpus_round_trips_through_json() -> None:
    corpus = DevCorpus("v2.0.36", [PackageRelease(TIME, Release("time", "0.1.0.0"))])
    assert DevCorpus.from_json(corpus.to_json()) == corpus


def test_dev_corpus_refuses_an_unnamed_package() -> None:
    with pytest.raises(DevError):
        DevCorpus.from_json(
            f'{{"base": "v2.0.36", "packages": [{{"hash": "{TIME}", "name": null}}]}}'
        )


async def test_open_corpus_reads_the_packages_from_the_store(packages: PackageStore) -> None:
    store = await full_store(packages)
    listed = DevCorpus("v2.0.36", [PackageRelease(TIME, Release("time", "0.1.0.0"))])
    corpus = await open_corpus(store, listed)
    assert corpus.packages == listed.packages
    assert corpus.library == store.library


@pytest.mark.parametrize(
    "listed",
    [
        DevCorpus("v2.0.36", [PackageRelease("0x" + "9" * 32, Release("x", "0.1.0.0"))]),
        DevCorpus("v2.0.35", []),
    ],
)
async def test_open_corpus_requires_the_packages_and_the_base_in_the_store(
    packages: PackageStore, listed: DevCorpus
) -> None:
    with pytest.raises(DevError):
        await open_corpus(await full_store(packages), listed)


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


async def test_select_skips_an_invalid_release(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_pages(router, [HOT])
    selection = await select(await full_store(packages), PackageBudget(10), {TIME})
    assert [package.hash for package in selection.corpus.packages] == [HTTP_OLD, JSON]
