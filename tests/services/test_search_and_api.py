from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jendhub.db.pool import Pool, open_pool
from jendhub.hub.api import Release
from jendhub.services.api.app import create_app as create_api
from jendhub.services.search.app import SearchResources
from jendhub.services.search.app import create_app as create_search
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import VectorIndex, open_vector_index
from tests.indexing.live import KIT_V2, WordEmbedder, index_live, seed, store_package

SEARCH_URL = "http://search.test"
DOUBLE = "kit@1.0.0.0/main:double"
QUADRUPLE = "app@1.0.0.0/main:quadruple"
BROKEN_SOURCE = "import Base\nimport ./missing.bend as M\n\ndef broken(n: Nat) -> Nat:\n  n\n"


@asynccontextmanager
async def running(app: FastAPI, base_url: str) -> AsyncGenerator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=base_url) as client,
    ):
        yield client


@pytest.fixture
async def search_app(
    pool: Pool,
    vectors: VectorIndex,
    database: DatabaseSettings,
    qdrant_settings: QdrantSettings,
    tmp_path: Path,
) -> FastAPI:
    packages = await seed(pool)
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN_SOURCE)
    await index_live(pool, vectors, tmp_path)

    @asynccontextmanager
    async def resources() -> AsyncGenerator[SearchResources]:
        async with open_pool(database) as own, open_vector_index(qdrant_settings) as index:
            yield SearchResources(own, index, WordEmbedder())

    return create_search(resources)


@pytest.fixture
async def search(search_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    async with running(search_app, SEARCH_URL) as client:
        yield client


@pytest.fixture
async def api(
    search_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient]:
    monkeypatch.setenv("JENDHUB_SEARCH_URL", SEARCH_URL)
    async with (
        search_app.router.lifespan_context(search_app),
        running(create_api(httpx.ASGITransport(search_app)), "http://api.test") as client,
    ):
        yield client


async def test_search_finds_a_definition(search: httpx.AsyncClient) -> None:
    response = await search.get("/search", params={"q": "double a number", "limit": 2})
    assert response.status_code == httpx.codes.OK
    [first, *_] = response.json()["results"]
    assert first["id"] == DOUBLE
    assert first["package"] == "kit"


async def test_a_definition_shows_its_uses_and_users(search: httpx.AsyncClient) -> None:
    response = await search.get(f"/definitions/{DOUBLE}")
    assert response.status_code == httpx.codes.OK
    page = response.json()
    assert page["uses"] == ["Nat", "Nat.add"]
    assert page["used_by"] == [QUADRUPLE]
    assert page["used_by_total"] == 1


async def test_used_by_is_paged(search: httpx.AsyncClient) -> None:
    page = (await search.get("/definitions/Nat", params={"used_by_limit": 1})).json()
    assert page["used_by_total"] == 3
    assert len(page["used_by"]) == 1


async def test_the_search_service_is_ready_with_a_live_index(search: httpx.AsyncClient) -> None:
    assert (await search.get("/readyz")).status_code == httpx.codes.OK
    assert (await search.get("/healthz")).json() == {"status": "ok"}
    await search.get("/search", params={"q": "double"})
    assert "jendhub_http_request_seconds" in (await search.get("/metrics")).text


async def test_the_api_answers_in_json(api: httpx.AsyncClient) -> None:
    response = await api.get("/v1/search", params={"q": "double a number"})
    assert response.headers["content-type"] == "application/json"
    assert response.json()["results"][0]["id"] == DOUBLE


async def test_the_api_answers_in_text(api: httpx.AsyncClient) -> None:
    headers = {"accept": "text/plain"}
    response = await api.get("/v1/search", params={"q": "double a number"}, headers=headers)
    assert response.text.startswith(f"1. {DOUBLE}\n   # Doubles a number.\n")
    page = await api.get(f"/v1/definitions/{DOUBLE}", headers=headers)
    assert page.text.startswith(f"{DOUBLE} (def, kit@1.0.0.0, main.bend, lines 3-5)")
    assert f"Used by (1):\n  {QUADRUPLE}\n" in page.text


async def test_the_api_reports_an_unknown_definition(api: httpx.AsyncClient) -> None:
    assert (await api.get("/v1/definitions/nothing")).status_code == httpx.codes.NOT_FOUND


@pytest.mark.parametrize("params", [{"q": ""}, {"q": "map", "limit": 0}, {"q": "x" * 1001}])
async def test_the_api_refuses_an_invalid_query(
    api: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await api.get("/v1/search", params=params)
    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_the_api_reports_a_search_index_that_is_not_ready(
    database: DatabaseSettings, qdrant_settings: QdrantSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def resources() -> AsyncGenerator[SearchResources]:
        async with open_pool(database) as own, open_vector_index(qdrant_settings) as index:
            yield SearchResources(own, index, WordEmbedder())

    empty = create_search(resources)
    monkeypatch.setenv("JENDHUB_SEARCH_URL", SEARCH_URL)
    async with (
        empty.router.lifespan_context(empty),
        running(create_api(httpx.ASGITransport(empty)), "http://api.test") as api,
    ):
        response = await api.get("/v1/search", params={"q": "double"})
        assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
        assert (await api.get("/readyz")).status_code == httpx.codes.SERVICE_UNAVAILABLE


async def test_a_package_shows_its_releases_and_definitions(search: httpx.AsyncClient) -> None:
    page = (await search.get("/packages/kit")).json()
    [newer, indexed] = page["releases"]
    assert (newer["version"], newer["hash"], newer["state"]) == ("2.0.0.0", KIT_V2, "invalid")
    assert newer["reason"].startswith("kit@2.0.0.0/main does not load")
    assert (indexed["version"], indexed["state"], indexed["reason"]) == ("1.0.0.0", "indexed", None)
    assert [definition["id"] for definition in page["definitions"]] == [DOUBLE]
    assert page["definitions"][0]["head"].startswith("# Doubles a number.")
    assert page["definitions_total"] == 1


async def test_package_definitions_are_paged(search: httpx.AsyncClient) -> None:
    page = (await search.get("/packages/kit", params={"definitions_offset": 1})).json()
    assert page["definitions"] == []
    assert page["definitions_total"] == 1
    assert page["definitions_offset"] == 1


async def test_the_status_shows_the_live_generation_and_invalid_releases(
    search: httpx.AsyncClient,
) -> None:
    page = (await search.get("/status")).json()
    [generation] = page["generations"]
    assert generation["state"] == "live"
    assert generation["releases"] == 3
    assert (generation["jobs"]["done"], generation["jobs"]["invalid"]) == (3, 1)
    [invalid] = page["invalid_releases"]
    assert (invalid["name"], invalid["version"], invalid["hash"]) == ("kit", "2.0.0.0", KIT_V2)
    assert page["invalid_releases_total"] == 1


async def test_the_api_shows_packages_and_status_in_text(api: httpx.AsyncClient) -> None:
    headers = {"accept": "text/plain"}
    package = await api.get("/v1/packages/kit", headers=headers)
    assert package.text.startswith("kit\n\nReleases (2):\n  2.0.0.0  ")
    assert f"Definitions (1):\n  {DOUBLE}\n   # Doubles a number.\n" in package.text
    status = await api.get("/v1/status", headers=headers)
    assert status.text.startswith("Generation ")
    assert f"Invalid releases (1):\n  kit@2.0.0.0 ({KIT_V2})" in status.text


async def test_the_api_reports_an_unknown_package(api: httpx.AsyncClient) -> None:
    assert (await api.get("/v1/packages/nothing")).status_code == httpx.codes.NOT_FOUND


async def test_the_status_needs_no_live_index(
    database: DatabaseSettings, qdrant_settings: QdrantSettings
) -> None:
    @asynccontextmanager
    async def resources() -> AsyncGenerator[SearchResources]:
        async with open_pool(database) as own, open_vector_index(qdrant_settings) as index:
            yield SearchResources(own, index, WordEmbedder())

    async with running(create_search(resources), SEARCH_URL) as search:
        page = (await search.get("/status")).json()
    assert page["generations"] == []
    assert page["invalid_releases_total"] == 0
