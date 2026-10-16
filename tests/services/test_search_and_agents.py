from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jendhub.db.pool import Pool, open_pool
from jendhub.hub.api import Release
from jendhub.services.agents.app import create_app as create_agents
from jendhub.services.search.app import SearchResources
from jendhub.services.search.app import create_app as create_search
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import VectorIndex, open_vector_index
from tests.indexing.live import APP, KIT_V2, WordEmbedder, index_live, seed, store_package

SEARCH_URL = "http://search.test"
AGENTS_URL = "https://agents.test"
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
async def agents(
    search_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient]:
    monkeypatch.setenv("JENDHUB_SEARCH_URL", SEARCH_URL)
    monkeypatch.setenv("JENDHUB_AGENTS_URL", AGENTS_URL)
    async with (
        search_app.router.lifespan_context(search_app),
        running(create_agents(httpx.ASGITransport(search_app)), "http://agents.test") as client,
    ):
        yield client


async def test_search_finds_a_definition(search: httpx.AsyncClient) -> None:
    response = await search.get("/search", params={"q": "double a number", "limit": 2})
    assert response.status_code == httpx.codes.OK
    [first, *_] = response.json()["results"]
    assert first["id"] == DOUBLE
    assert first["package"] == "kit"
    assert first["import_line"] == "import kit@1.0.0.0/main.bend as Kit"
    assert first["call_name"] == "Kit.double"
    assert first["file"] == "kit@1.0.0.0/main.bend"


async def test_search_pages_through_one_fixed_ranking(search: httpx.AsyncClient) -> None:
    query = {"q": "double a number"}
    every = (await search.get("/search", params=query | {"limit": 50})).json()
    later = (await search.get("/search", params=query | {"limit": 1, "offset": 1})).json()
    assert every["total"] == len(every["results"])
    assert later["total"] == every["total"]
    assert later["offset"] == 1
    assert later["results"] == every["results"][1:2]


async def test_search_refuses_an_offset_past_the_ranking(search: httpx.AsyncClient) -> None:
    response = await search.get("/search", params={"q": "double", "offset": 50})
    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_a_definition_shows_its_uses_and_users(search: httpx.AsyncClient) -> None:
    response = await search.get(f"/definitions/{DOUBLE}")
    assert response.status_code == httpx.codes.OK
    page = response.json()
    assert page["uses"] == ["Nat", "Nat.add"]
    assert page["used_by"] == [QUADRUPLE]
    assert page["used_by_total"] == 1
    assert page["imports"] == ["import Base"]


async def test_a_definition_gives_the_import_lines_that_its_code_uses(
    search: httpx.AsyncClient,
) -> None:
    page = (await search.get(f"/definitions/{QUADRUPLE}")).json()
    assert page["imports"] == ["import Base", "import kit@1.0.0.0/main.bend as K"]
    assert page["import_line"] == "import app@1.0.0.0/main.bend as App"
    assert page["call_name"] == "App.quadruple"


async def test_a_base_definition_needs_only_base(search: httpx.AsyncClient) -> None:
    page = (await search.get("/definitions/Nat.add")).json()
    assert (page["imports"], page["import_line"], page["call_name"], page["file"]) == (
        ["import Base"],
        "import Base",
        "Nat.add",
        "Base/base.bend",
    )


async def test_a_file_names_exact_releases_in_its_imports(search: httpx.AsyncClient) -> None:
    page = (await search.get("/files/kit@2.0.0.0/main.bend")).json()
    assert (page["release"], page["hash"], page["path"]) == ("kit@2.0.0.0", KIT_V2, "main.bend")
    assert page["text"] == BROKEN_SOURCE.replace("./missing.bend", "kit@2.0.0.0/missing.bend")


async def test_a_file_is_found_by_the_hash_of_its_package(search: httpx.AsyncClient) -> None:
    page = (await search.get(f"/files/{APP}/main.bend")).json()
    assert page["release"] == "app@1.0.0.0"
    assert "import kit@1.0.0.0/main.bend as K" in page["text"]


async def test_the_files_of_a_release_and_of_base_are_listed(search: httpx.AsyncClient) -> None:
    assert (await search.get("/files/app@1.0.0.0")).json()["paths"] == ["main.bend"]
    assert (await search.get("/files/Base")).json()["paths"] == ["base.bend"]
    base = (await search.get("/files/Base/base.bend")).json()
    assert "Nat.add" in base["text"]


@pytest.mark.parametrize(
    "path", ["/files/kit@9.0.0.0/main.bend", "/files/kit@1.0.0.0/other.bend", "/files/nothing"]
)
async def test_an_unknown_file_is_not_found(search: httpx.AsyncClient, path: str) -> None:
    assert (await search.get(path)).status_code == httpx.codes.NOT_FOUND


async def test_used_by_is_paged(search: httpx.AsyncClient) -> None:
    page = (await search.get("/definitions/Nat", params={"used_by_limit": 1})).json()
    assert page["used_by_total"] == 3
    assert len(page["used_by"]) == 1


async def test_the_search_service_is_ready_with_a_live_index(search: httpx.AsyncClient) -> None:
    assert (await search.get("/readyz")).status_code == httpx.codes.OK
    assert (await search.get("/healthz")).json() == {"status": "ok"}
    await search.get("/search", params={"q": "double"})
    assert "jendhub_http_request_seconds" in (await search.get("/metrics")).text


async def test_the_agent_interface_answers_in_text_for_any_accept_header(
    agents: httpx.AsyncClient,
) -> None:
    params = {"q": "double a number", "limit": 1}
    headers = {"accept": "application/json"}
    response = await agents.get("/search", params=params, headers=headers)
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith(
        f"1. Kit.double ({DOUBLE})\n   import kit@1.0.0.0/main.bend as Kit\n"
        "   # Doubles a number.\n"
    )
    assert f"   source: {AGENTS_URL}/definitions/{DOUBLE}\n" in response.text
    assert response.text.endswith(f"More: {AGENTS_URL}/search?q=double+a+number&limit=1&offset=1\n")
    page = await agents.get(f"/definitions/{QUADRUPLE}")
    assert page.text.startswith(
        f"App.quadruple ({QUADRUPLE}) (def, app@1.0.0.0, lines 4-5)\n"
        "Use: import app@1.0.0.0/main.bend as App, then call App.quadruple.\n"
        f"File: {AGENTS_URL}/files/app@1.0.0.0/main.bend\n\n"
        "import Base\nimport kit@1.0.0.0/main.bend as K\n\ndef quadruple(n: Nat) -> Nat:\n"
    )


async def test_the_agent_interface_gives_a_file_as_plain_text(agents: httpx.AsyncClient) -> None:
    response = await agents.get("/files/kit@2.0.0.0/main.bend")
    assert response.text == BROKEN_SOURCE.replace("./missing.bend", "kit@2.0.0.0/missing.bend")
    listing = await agents.get("/files/kit@1.0.0.0")
    assert listing.text == f"kit@1.0.0.0\n\n  {AGENTS_URL}/files/kit@1.0.0.0/main.bend\n"
    assert (await agents.get("/files/kit@9.0.0.0")).status_code == httpx.codes.NOT_FOUND


async def test_the_agent_interface_tells_agents_how_to_use_it(agents: httpx.AsyncClient) -> None:
    guide = await agents.get("/llms.txt")
    assert guide.headers["content-type"].startswith("text/plain")
    assert f"GET {AGENTS_URL}/search?q=<query>" in guide.text
    assert (await agents.get("/robots.txt")).text == "User-agent: *\nAllow: /\n"


async def test_the_agent_interface_sends_security_headers(agents: httpx.AsyncClient) -> None:
    headers = (await agents.get("/healthz")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["strict-transport-security"] == "max-age=31536000"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


async def test_the_agent_interface_reports_an_unknown_definition_in_text(
    agents: httpx.AsyncClient,
) -> None:
    response = await agents.get("/definitions/nothing")
    assert response.status_code == httpx.codes.NOT_FOUND
    assert response.text == "no definition nothing\n"
    assert (await agents.get("/nothing")).text == "Not Found\n"


@pytest.mark.parametrize("params", [{"q": ""}, {"q": "map", "limit": 0}, {"q": "x" * 1001}])
async def test_the_agent_interface_refuses_an_invalid_query_in_text(
    agents: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await agents.get("/search", params=params)
    assert response.status_code == httpx.codes.BAD_REQUEST
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("Invalid parameter ")


async def test_the_agent_interface_reports_a_search_index_that_is_not_ready(
    database: DatabaseSettings, qdrant_settings: QdrantSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def resources() -> AsyncGenerator[SearchResources]:
        async with open_pool(database) as own, open_vector_index(qdrant_settings) as index:
            yield SearchResources(own, index, WordEmbedder())

    empty = create_search(resources)
    monkeypatch.setenv("JENDHUB_SEARCH_URL", SEARCH_URL)
    monkeypatch.setenv("JENDHUB_AGENTS_URL", AGENTS_URL)
    async with (
        empty.router.lifespan_context(empty),
        running(create_agents(httpx.ASGITransport(empty)), "http://agents.test") as agents,
    ):
        response = await agents.get("/search", params={"q": "double"})
        assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
        assert (await agents.get("/readyz")).status_code == httpx.codes.SERVICE_UNAVAILABLE


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


async def test_the_agent_interface_shows_packages_and_status_in_text(
    agents: httpx.AsyncClient,
) -> None:
    package = await agents.get("/packages/kit")
    assert package.text.startswith("kit\n\nReleases (2):\n  2.0.0.0  ")
    assert f"indexed  {AGENTS_URL}/files/kit@1.0.0.0\n" in package.text
    assert f"Definitions (1):\n  {DOUBLE}\n   # Doubles a number.\n" in package.text
    status = await agents.get("/status")
    assert status.text.startswith("Generation ")
    assert f"Invalid releases (1):\n  kit@2.0.0.0 ({KIT_V2})" in status.text


async def test_the_agent_interface_reports_an_unknown_package(agents: httpx.AsyncClient) -> None:
    assert (await agents.get("/packages/nothing")).status_code == httpx.codes.NOT_FOUND


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
