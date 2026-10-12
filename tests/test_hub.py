import asyncio

import httpx
import pytest
import respx

from jendhub import hub
from jendhub.hub import (
    HUB,
    HubError,
    Listing,
    Package,
    Rejection,
    Release,
    fetch_package,
    new_listings,
)
from tests.hub_fixture import listing, serve_package, serve_pages

HASH_A = "0x" + "a" * 32
HASH_B = "0x" + "b" * 32
HASH_C = "0x" + "c" * 32


def test_parse_listing_reads_a_named_package() -> None:
    parsed = hub.parse_listing({"hash": HASH_A, "name": "list-kit", "version": "0.1.0.0"})
    assert parsed == Listing(HASH_A, Release("list-kit", "0.1.0.0"))


def test_parse_listing_reads_an_unnamed_package() -> None:
    assert hub.parse_listing({"hash": HASH_A, "name": None}) == Listing(HASH_A, None)


@pytest.mark.parametrize(
    "item",
    [
        {"hash": HASH_A, "name": "Bad_Name", "version": "0.1.0.0"},
        {"hash": HASH_A, "name": "kit", "version": "0.1.0"},
        {"hash": HASH_A, "name": "kit", "version": None},
    ],
)
def test_parse_listing_rejects_an_invalid_release(item: hub.Json) -> None:
    assert isinstance(hub.parse_listing(item), Rejection)


@pytest.mark.parametrize("item", [{"hash": "0x123", "name": None}, {"name": None}, []])
def test_parse_listing_fails_on_a_listing_without_a_valid_hash(item: hub.Json) -> None:
    with pytest.raises(HubError):
        hub.parse_listing(item)


def test_parse_manifest_accepts_nested_paths() -> None:
    assert hub.parse_manifest("ab proofs/sorted.bend\ncd LICENSE\n") == {
        "proofs/sorted.bend": "ab",
        "LICENSE": "cd",
    }


@pytest.mark.parametrize("path", ["main.bend", "proofs/sorted.bend", "LICENSE", "a-b_c.d.bend"])
def test_valid_path_accepts_plain_relative_paths(path: str) -> None:
    assert hub.valid_path(path)


@pytest.mark.parametrize(
    "path", ["../escape.bend", "/root.bend", "a//b.bend", "a\\b.bend", "a b.bend", "~.bend", ""]
)
def test_valid_path_rejects_other_paths(path: str) -> None:
    assert not hub.valid_path(path)


def test_new_listings_stops_at_the_first_known_hash(router: respx.MockRouter) -> None:
    serve_pages(router, [[listing(HASH_A), listing(HASH_B), listing(HASH_C)]])
    found = asyncio.run(_new_listings({HASH_B}))
    assert [entry.hash for entry in found] == [HASH_A]


def test_new_listings_skips_entries_that_shift_into_the_next_page(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "PAGE_SIZE", 2)
    serve_pages(
        router,
        [[listing(HASH_A), listing(HASH_B)], [listing(HASH_B), listing(HASH_C)], []],
    )
    found = asyncio.run(_new_listings(set()))
    assert [entry.hash for entry in found] == [HASH_A, HASH_B, HASH_C]


def test_fetch_package_fetches_only_bend_files(router: respx.MockRouter) -> None:
    files = {"main.bend": b"def main() -> Nat:\n  0n\n", "effs/time.c": b"int x;"}
    hash_ = serve_package(router, files)
    package = asyncio.run(_fetch_package(Listing(hash_, None)))
    assert isinstance(package, Package)
    assert package.files == {"main.bend": files["main.bend"]}
    assert not router.get(f"{HUB}/{hash_}/effs/time.c").called


def test_fetch_package_rejects_a_package_with_an_invalid_file_name(
    router: respx.MockRouter,
) -> None:
    hash_ = serve_package(router, {"main.bend": b"main", "notes~.txt": b"notes"})
    rejection = Rejection(hash_, "invalid file name 'notes~.txt'")
    assert asyncio.run(_fetch_package(Listing(hash_, None))) == rejection
    assert not router.get(f"{HUB}/{hash_}/main.bend").called


def test_fetch_package_rejects_a_file_that_does_not_match_its_manifest(
    router: respx.MockRouter,
) -> None:
    hash_ = serve_package(router, {"main.bend": b"original"})
    router.get(f"{HUB}/{hash_}/main.bend").mock(httpx.Response(200, content=b"changed"))
    with pytest.raises(HubError):
        asyncio.run(_fetch_package(Listing(hash_, None)))


def test_fetch_package_rejects_a_manifest_that_does_not_match_the_hash(
    router: respx.MockRouter,
) -> None:
    serve_package(router, {"main.bend": b"original"})
    router.get(f"{HUB}/{HASH_A}/manifest").mock(httpx.Response(200, content=b"ab main.bend\n"))
    with pytest.raises(HubError):
        asyncio.run(_fetch_package(Listing(HASH_A, None)))


async def _new_listings(known: set[str]) -> list[Listing | Rejection]:
    async with httpx.AsyncClient() as client:
        return await new_listings(client, known)


async def _fetch_package(entry: Listing) -> Package | Rejection:
    async with httpx.AsyncClient() as client:
        return await fetch_package(client, entry)
