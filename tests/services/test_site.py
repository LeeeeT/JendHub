from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jendhub.db.pool import Pool, open_pool
from jendhub.hub.api import Release
from jendhub.search.ranking import load_model
from jendhub.services.site.app import create_app
from jendhub.services.site.views import Resources
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import PRODUCTION, VectorIndex, open_vector_index
from tests.indexing.live import APP, KIT_V2, WordEmbedder, index_live, seed, store_package

SITE_URL = "https://jend.test"
DOUBLE = "kit@1.0.0.0/main:double"
DOUBLE_SOURCE = f"{SITE_URL}/src/kit@1.0.0.0/main.bend?def=double"
QUADRUPLE = "app@1.0.0.0/main:quadruple"
BROKEN_SOURCE = "import Base\nimport ./missing.bend as M\n\ndef broken(n: Nat) -> Nat:\n  n\n"
FIXED_BROKEN = BROKEN_SOURCE.replace("./missing.bend", "kit@2.0.0.0/missing.bend")

type Opener = Callable[[], AbstractAsyncContextManager[Resources]]


def opener(database: DatabaseSettings, qdrant: QdrantSettings) -> Opener:
    @asynccontextmanager
    async def resources() -> AsyncGenerator[Resources]:
        async with open_pool(database) as own, open_vector_index(qdrant) as index:
            yield Resources(own, index, WordEmbedder(), load_model())

    return resources


@asynccontextmanager
async def running(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=SITE_URL) as client,
    ):
        yield client


@pytest.fixture(autouse=True)
def site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JENDHUB_SITE_URL", SITE_URL)


async def build_index(pool: Pool, vectors: VectorIndex, library: Path) -> None:
    packages = await seed(pool)
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN_SOURCE)
    await index_live(pool, vectors, library)


@pytest.fixture
async def indexed(pool: Pool, vectors: VectorIndex, tmp_path: Path) -> None:
    await build_index(pool, vectors, tmp_path)


@pytest.fixture
async def site(
    pool: Pool,
    vectors: VectorIndex,
    tmp_path: Path,
    database: DatabaseSettings,
    qdrant_settings: QdrantSettings,
) -> AsyncGenerator[httpx.AsyncClient]:
    await build_index(pool, vectors, tmp_path)
    async with running(create_app(opener(database, qdrant_settings))) as client:
        yield client


@pytest.fixture
async def empty_site(
    database: DatabaseSettings, qdrant_settings: QdrantSettings
) -> AsyncGenerator[httpx.AsyncClient]:
    async with running(create_app(opener(database, qdrant_settings))) as client:
        yield client


type StoredSearch = tuple[str, str | None, str, int, int, list[str]]


async def stored_searches(pool: Pool) -> list[StoredSearch]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT client, user_agent, query, page_offset, page_limit, results"
            " FROM searches ORDER BY id"
        )
        return await cursor.fetchall()


async def test_agents_get_results_with_their_source_and_import_line(
    site: httpx.AsyncClient,
) -> None:
    params = {"q": "double a number", "limit": 1}
    response = await site.get("/search.txt", params=params, headers={"accept": "text/html"})
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith(
        f"1. Kit.double ({DOUBLE_SOURCE})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Doubles a number.\n"
    )
    assert response.text.endswith(
        f"More: {SITE_URL}/search.txt?q=double+a+number&limit=1&offset=1\n"
    )


async def test_search_pages_through_one_fixed_ranking(site: httpx.AsyncClient) -> None:
    every = (await site.get("/search.txt", params={"q": "double a number", "limit": 50})).text
    later = (await site.get("/search.txt", params={"q": "double", "limit": 1, "offset": 1})).text
    assert every.startswith("1. ")
    assert later.startswith("2. ")
    assert (await site.get("/search.txt", params={"q": "x", "offset": 50})).status_code == 400


async def test_each_search_is_stored_with_its_client_and_whole_ranking(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    await site.get("/search.txt", params={"q": "double", "limit": 1}, headers={"user-agent": "a/1"})
    await site.get("/search", params={"q": "double a number"}, headers={"user-agent": "b/2"})
    [agents, web] = await stored_searches(pool)
    assert agents[:5] == ("agents", "a/1", "double", 0, 1)
    assert web[:5] == ("web", "b/2", "double a number", 0, 20)
    assert web[5][0] == DOUBLE
    assert len(web[5]) > 1


async def test_a_definition_gives_its_import_lines_and_its_uses(site: httpx.AsyncClient) -> None:
    response = await site.get("/src/app@1.0.0.0/main.bend", params={"def": "quadruple"})
    assert response.text == (
        "App.quadruple (def, app@1.0.0.0, lines 4-5)\n"
        "Use: import app@1.0.0.0/main.bend as App, then call App.quadruple.\n"
        f"File: {SITE_URL}/src/app@1.0.0.0/main.bend\n\n"
        "import Base\nimport kit@1.0.0.0/main.bend as K\n\n"
        "def quadruple(n: Nat) -> Nat:\n  K.double(K.double(n))\n\n"
        f"Uses (2):\n  {SITE_URL}/src/Base/base.bend?def=Nat\n  {DOUBLE_SOURCE}\n\n"
        "Used by (0):\n"
    )


async def test_a_base_definition_is_under_base(site: httpx.AsyncClient) -> None:
    response = await site.get("/src/Base/base.bend", params={"def": "Nat.add"})
    assert response.text.startswith("Nat.add (def, Base, lines ")
    assert "Use: import Base, then call Nat.add.\n" in response.text


async def test_a_file_names_exact_releases_in_its_imports(site: httpx.AsyncClient) -> None:
    assert (await site.get("/src/kit@2.0.0.0/main.bend")).text == FIXED_BROKEN
    by_hash = (await site.get(f"/src/{APP}/main.bend")).text
    assert "import kit@1.0.0.0/main.bend as K" in by_hash
    assert "Nat.add" in (await site.get("/src/Base/base.bend")).text


async def test_the_files_of_a_release_are_listed(site: httpx.AsyncClient) -> None:
    response = await site.get("/src/kit@1.0.0.0/")
    assert response.text == f"kit@1.0.0.0\n\n  {SITE_URL}/src/kit@1.0.0.0/main.bend\n"


@pytest.mark.parametrize(
    "path",
    [
        "/src/kit@9.0.0.0/main.bend",
        "/src/kit@1.0.0.0/other.bend",
        "/src/nothing/",
        "/src/kit@1.0.0.0/main.bend?def=nothing",
        "/packages/nothing.txt",
    ],
)
async def test_agents_get_a_missing_thing_as_text(site: httpx.AsyncClient, path: str) -> None:
    response = await site.get(path)
    assert response.status_code == httpx.codes.NOT_FOUND
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("params", [{"q": ""}, {"q": "map", "limit": 0}, {"q": "x" * 1001}])
async def test_agents_get_an_invalid_query_as_text(
    site: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await site.get("/search.txt", params=params)
    assert response.status_code == httpx.codes.BAD_REQUEST
    assert response.text.startswith("Invalid parameter ")


async def test_agents_get_a_package_and_the_status_as_text(site: httpx.AsyncClient) -> None:
    package = (await site.get("/packages/kit.txt")).text
    assert package.startswith("kit\n\nReleases (2):\n  2.0.0.0  ")
    assert f"indexed  {SITE_URL}/src/kit@1.0.0.0/\n" in package
    assert f"  Kit.double ({DOUBLE_SOURCE})\n   # Doubles a number.\n" in package
    status = (await site.get("/status.txt")).text
    assert "  Jobs: 0 queued, 0 running, 3 indexed, 0 superseded, 1 invalid, 0 failed\n" in status
    assert f"Invalid releases (1):\n  kit@2.0.0.0 ({KIT_V2})" in status
    assert f"   At {SITE_URL}/src/kit@2.0.0.0/main.bend line 2.\n" in status
    assert "   Files that do not load: main.bend.\n" in status


async def test_agents_read_the_guide_and_robots_allow_them(site: httpx.AsyncClient) -> None:
    guide = (await site.get("/llms.txt")).text
    assert f"GET {SITE_URL}/search.txt?q=<query>" in guide
    assert f"GET {SITE_URL}/src/<name@version>/<path>" in guide
    assert "packages.txt" not in guide
    assert (await site.get("/robots.txt")).text == "User-agent: *\nDisallow: /search?\n"


async def test_people_get_the_results_as_a_page(site: httpx.AsyncClient) -> None:
    response = await site.get("/search", params={"q": "double a number"})
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert f'href="/definitions/{DOUBLE}"' in html
    assert 'href="/packages/kit"' in html
    assert "import kit@1.0.0.0/main.bend as Kit" in html
    assert 'type="text/plain" href="/search.txt?q=double+a+number"' in html
    assert "<script" not in html


async def test_an_empty_search_goes_home(site: httpx.AsyncClient) -> None:
    response = await site.get("/search", params={"q": " "})
    assert response.status_code == httpx.codes.SEE_OTHER
    assert response.headers["location"] == "/"


async def test_people_get_a_definition_with_its_lines_and_uses(site: httpx.AsyncClient) -> None:
    html = (await site.get(f"/definitions/{QUADRUPLE}")).text
    assert 'href="/files/app@1.0.0.0/main.bend#L5"' in html
    assert f'href="/definitions/{DOUBLE}"' in html
    assert 'href="/files/app@1.0.0.0/main.bend?lines=4-5#L4"' in html
    assert "App.quadruple" in html
    assert 'href="/src/app@1.0.0.0/main.bend?def=quadruple"' in html
    assert "No indexed definition uses this definition." in html
    used = (await site.get(f"/definitions/{DOUBLE}")).text
    assert f'href="/definitions/{QUADRUPLE}"' in used


async def test_people_get_a_package_its_files_and_a_file(site: httpx.AsyncClient) -> None:
    package = (await site.get("/packages/kit")).text
    assert 'Search uses release <a href="/files/kit@1.0.0.0">1.0.0.0</a>' in package
    assert 'class="badge invalid"' in package
    assert 'href="/files/kit@2.0.0.0/main.bend#L2">main.bend:2</a>' in package
    assert f'href="/definitions/{DOUBLE}">double</a>' in package
    assert "Doubles a number." in package
    files = (await site.get("/files/kit@1.0.0.0")).text
    assert 'href="/files/kit@1.0.0.0/main.bend"' in files
    assert "1 definition<" in files
    file = (await site.get("/files/app@1.0.0.0/main.bend", params={"lines": "4-5"})).text
    assert '<span class="line marked" id="L5">' in file
    assert '<span class="line" id="L3">' in file
    assert 'href="/files/kit@1.0.0.0/main.bend"' in file
    assert 'href="/files/Base/base.bend"' in file
    assert f'href="/definitions/{QUADRUPLE}">quadruple</a>' in file
    assert (await site.get("/packages")).status_code == httpx.codes.NOT_FOUND


async def test_people_get_the_status_and_the_home_page(site: httpx.AsyncClient) -> None:
    status = (await site.get("/status")).text
    assert "Indexed" in status
    assert PRODUCTION.label in status
    assert 'href="/files/kit@2.0.0.0/main.bend#L2">main.bend:2</a>' in status
    home = (await site.get("/")).text
    assert 'href="/search?q=decompress+gzip+data"' in home
    assert f'href="/llms.txt">{SITE_URL}/llms.txt</a>' in home
    assert "search.txt" not in home
    assert "<b>2</b> packages" in home


async def test_people_get_a_missing_page_as_html(site: httpx.AsyncClient) -> None:
    response = await site.get("/definitions/nothing")
    assert response.status_code == httpx.codes.NOT_FOUND
    assert response.headers["content-type"].startswith("text/html")
    assert "The index has no definition nothing." in response.text
    assert "<title>Not Found · JendHub</title>" in response.text


async def test_every_answer_has_the_security_headers(site: httpx.AsyncClient) -> None:
    for path in ("/", "/search.txt?q=double", "/style.css"):
        headers = (await site.get(path)).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert "script-src" not in headers["content-security-policy"]
        assert "style-src 'self'" in headers["content-security-policy"]


async def test_the_metrics_are_not_on_the_public_port(site: httpx.AsyncClient) -> None:
    assert (await site.get("/metrics")).status_code == httpx.codes.NOT_FOUND
    assert (await site.get("/readyz")).status_code == httpx.codes.OK


@pytest.mark.usefixtures("indexed")
async def test_searches_from_one_client_are_limited(
    database: DatabaseSettings, qdrant_settings: QdrantSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JENDHUB_SEARCH_BURST", "1")
    monkeypatch.setenv("JENDHUB_SEARCHES_PER_MINUTE", "1")
    monkeypatch.setenv("JENDHUB_CLIENT_IP_HEADER", "cf-connecting-ip")
    async with running(create_app(opener(database, qdrant_settings))) as client:
        first = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
        second = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
        other = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "2.2.2.2"})
        page = await client.get("/search?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
    assert first.status_code == httpx.codes.OK
    assert second.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert second.text.startswith("Too many requests. Try again in ")
    assert int(second.headers["retry-after"]) > 0
    assert other.status_code == httpx.codes.OK
    assert page.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert page.headers["content-type"].startswith("text/html")


async def test_without_a_live_index_search_waits_and_the_status_answers(
    empty_site: httpx.AsyncClient,
) -> None:
    response = await empty_site.get("/search.txt", params={"q": "double"})
    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.text == "The search index is not ready. Try again later.\n"
    assert (await empty_site.get("/readyz")).status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert (await empty_site.get("/status.txt")).text.startswith("No generation.\n")
