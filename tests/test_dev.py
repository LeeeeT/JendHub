import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from jendhub.base import SOURCE, Base
from jendhub.dev import (
    Budget,
    DefinitionBudget,
    DevCorpus,
    DevError,
    DevPackage,
    PackageBudget,
    Selection,
    build,
    count_definitions,
    select,
)
from jendhub.hub import Listing, Package, Rejection, Release
from jendhub.mirror import Mirror
from tests.hub_fixture import listing, serve_pages

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


def full_mirror(root: Path) -> Mirror:
    full = Mirror(root)
    full.store_base(Base("v2.0.36", b"base"))
    sources = {
        HTTP_OLD: b"def get() -> Nat:\n  0n\n",
        HTTP_NEW: b"def get() -> Nat:\n  1n\n",
        TIME: b"type Instant is Data:\n  Instant{}\n\nlaw later:\n  Nat\n",
        JSON: b"def parse() -> Nat:\n  0n\n",
        UNNAMED: b"def main() -> Nat:\n  0n\n",
    }
    for hash_, source in sources.items():
        full.store_package(Package(Listing(hash_, None), {"main.bend": source}))
    full.reject(Rejection(REJECTED, "invalid file name 'a b.bend'"))
    return full


def select_hot(router: respx.MockRouter, root: Path, budget: Budget) -> Selection:
    serve_pages(router, [HOT])
    selection = asyncio.run(select(full_mirror(root), budget))
    assert selection.corpus.base_tag == "v2.0.36"
    return selection


def selected(router: respx.MockRouter, root: Path, budget: Budget) -> list[str]:
    return [package.hash for package in select_hot(router, root, budget).corpus.packages]


def test_count_definitions_counts_top_level_declarations() -> None:
    source = b"# def note\ntype T is Data:\n  T{}\ndef f() -> T:\n  def_x = 1\nlaw l:\n  T\n"
    assert count_definitions({"a.bend": source, "b.bend": b"def g() -> T:\n"}) == 4


def test_select_keeps_the_hottest_release_of_each_named_package(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    assert selected(router, tmp_path, PackageBudget(10)) == [HTTP_OLD, TIME, JSON]


def test_select_stops_at_the_package_budget(router: respx.MockRouter, tmp_path: Path) -> None:
    assert selected(router, tmp_path, PackageBudget(2)) == [HTTP_OLD, TIME]


def test_select_skips_a_package_that_exceeds_the_definition_budget(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    assert selected(router, tmp_path, DefinitionBudget(2)) == [HTTP_OLD, JSON]


def test_select_stops_when_the_definition_budget_is_used(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    assert selected(router, tmp_path, DefinitionBudget(1)) == [HTTP_OLD]


def test_select_counts_the_definitions_of_the_selected_packages(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    assert select_hot(router, tmp_path, PackageBudget(3)).definitions == 4


def test_select_requires_the_packages_in_the_full_corpus(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    serve_pages(router, [[listing("0x" + "9" * 32, "fresh", "0.1.0.0")]])
    with pytest.raises(DevError):
        asyncio.run(select(full_mirror(tmp_path), PackageBudget(1)))


def test_dev_corpus_round_trips_through_json() -> None:
    corpus = DevCorpus("v2.0.36", [DevPackage(TIME, Release("time", "0.1.0.0"))])
    assert DevCorpus.from_json(corpus.to_json()) == corpus


def test_dev_corpus_refuses_an_unnamed_package() -> None:
    with pytest.raises(DevError):
        DevCorpus.from_json(
            f'{{"base": "v2.0.36", "packages": [{{"hash": "{TIME}", "name": null}}]}}'
        )


def test_build_replaces_the_dev_corpus(router: respx.MockRouter, tmp_path: Path) -> None:
    full = full_mirror(tmp_path / "full")
    dev_root = tmp_path / "dev"
    (dev_root / "lib" / JSON).mkdir(parents=True)
    router.get(SOURCE.format(tag="v2.0.35")).mock(httpx.Response(200, content=b"old base"))
    corpus = DevCorpus("v2.0.35", [DevPackage(TIME, Release("time", "0.1.0.0"))])
    asyncio.run(build(full, dev_root, corpus))
    dev = Mirror(dev_root)
    assert dev.hashes() == {TIME}
    assert dev.read_package(TIME) == full.read_package(TIME)
    assert (dev_root / "lib" / "names" / "time@0.1.0.0").read_text() == f"{TIME}\n"
    assert dev.stored_base_tag() == "v2.0.35"
    assert dev.base_source.read_bytes() == b"old base"
    assert not (tmp_path / ".dev.staging").exists()
