import asyncio
from pathlib import Path

import httpx
import respx

from jendhub.base import LATEST_RELEASE, SOURCE, Base
from jendhub.hub import HUB
from jendhub.mirror import Mirror
from jendhub.sync import sync
from tests.hub_fixture import listing, serve_package, serve_pages


def serve_base(router: respx.MockRouter, tag: str, source: bytes) -> respx.Route:
    router.get(LATEST_RELEASE).mock(httpx.Response(200, json={"tag_name": tag}))
    return router.get(SOURCE.format(tag=tag)).mock(httpx.Response(200, content=source))


def test_sync_mirrors_new_packages_and_base(router: respx.MockRouter, tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    serve_base(router, "v2.0.36", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(new, "kit", "0.2.0.0"), listing(old, "kit", "0.1.0.0")]])
    asyncio.run(sync(mirror))
    assert mirror.hashes() == {old, new}
    assert (tmp_path / "lib" / "names" / "kit@0.2.0.0").read_text() == f"{new}\n"
    assert mirror.stored_base_tag() == "v2.0.36"


def test_sync_rejects_a_package_with_an_invalid_file_name_once(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    mirror = Mirror(tmp_path)
    serve_base(router, "v2.0.36", b"base")
    valid = serve_package(router, {"main.bend": b"main"})
    invalid = serve_package(router, {"main.bend": b"other", "notes~.txt": b"notes"})
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    asyncio.run(sync(mirror))
    assert mirror.hashes() == {valid}
    assert mirror.rejected_hashes() == {invalid}
    assert not (tmp_path / "lib" / "names" / "kit@0.2.0.0").exists()
    invalid_manifest = router.get(f"{HUB}/{invalid}/manifest")
    invalid_manifest.reset()
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    asyncio.run(sync(mirror))
    assert not invalid_manifest.called


def test_sync_rejects_an_invalid_release_without_fetching_it(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    mirror = Mirror(tmp_path)
    serve_base(router, "v2.0.36", b"base")
    invalid = serve_package(router, {"main.bend": b"main"})
    serve_pages(router, [[listing(invalid, "Bad_Name", "0.1.0.0")]])
    asyncio.run(sync(mirror))
    assert mirror.hashes() == set()
    assert mirror.rejected_hashes() == {invalid}
    assert not router.get(f"{HUB}/{invalid}/manifest").called


def test_sync_fetches_only_packages_newer_than_the_mirror(
    router: respx.MockRouter, tmp_path: Path
) -> None:
    mirror = Mirror(tmp_path)
    serve_base(router, "v2.0.36", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(old)]])
    asyncio.run(sync(mirror))
    old_manifest = router.get(f"{HUB}/{old}/manifest")
    old_manifest.reset()
    serve_pages(router, [[listing(new), listing(old)]])
    asyncio.run(sync(mirror))
    assert mirror.hashes() == {old, new}
    assert not old_manifest.called


def test_sync_keeps_a_current_base(router: respx.MockRouter, tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    mirror.store_base(Base("v2.0.36", b"base"))
    source = serve_base(router, "v2.0.36", b"base")
    serve_pages(router, [[]])
    asyncio.run(sync(mirror))
    assert not source.called
