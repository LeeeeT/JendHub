import asyncio
from asyncio import FIRST_COMPLETED
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors
from jendhub.hub.api import Release
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from jendhub.remote.openrouter import OpenRouterError
from jendhub.search.ranking import load_model
from jendhub.services.site.app import create_app
from jendhub.services.site.views import Resources
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import PRODUCTION, VectorIndex, open_vector_index
from tests.indexing.live import (
    APP,
    APP_SOURCE,
    KIT_V2,
    WordEmbedder,
    index_live,
    seed,
    store_package,
)

SITE_URL = "https://jend.test"
DOUBLE = "kit@1.0.0.0/main:double"
DOUBLE_READ = f"{SITE_URL}/read.txt?file_path=kit@1.0.0.0/main.bend&offset=3&limit=3"
QUADRUPLE = "app@1.0.0.0/main:quadruple"
BROKEN_SOURCE = "import Base\nimport ./missing.bend as M\n\ndef broken(n: Nat) -> Nat:\n  n\n"

type Opener = Callable[[], AbstractAsyncContextManager[Resources]]


class SilentEmbedder:
    async def embed(self, texts: list[str]) -> Vectors:
        raise OpenRouterError(f"OpenRouter did not answer: ReadTimeout ({len(texts)} texts)")


def opener(
    database: DatabaseSettings,
    qdrant: QdrantSettings,
    library: Library,
    embedder: Embedder | None = None,
) -> Opener:
    @asynccontextmanager
    async def resources() -> AsyncGenerator[Resources]:
        async with open_pool(database) as own, open_vector_index(qdrant) as index:
            yield Resources(own, library, index, embedder or WordEmbedder(), load_model())

    return resources


@asynccontextmanager
async def running(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    started, stop = asyncio.Event(), asyncio.Event()

    async def serve() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    server = asyncio.create_task(serve())
    await asyncio.wait([server, asyncio.create_task(started.wait())], return_when=FIRST_COMPLETED)
    if server.done():
        server.result()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=SITE_URL
        ) as client:
            yield client
    finally:
        stop.set()
        await server


@pytest.fixture(autouse=True)
def site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JENDHUB_SITE_URL", SITE_URL)


async def build_index(packages: PackageStore, vectors: VectorIndex) -> None:
    await seed(packages)
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN_SOURCE)
    await index_live(packages, vectors)


@pytest.fixture
async def indexed(packages: PackageStore, vectors: VectorIndex) -> None:
    await build_index(packages, vectors)


@pytest.fixture
async def site(
    packages: PackageStore,
    vectors: VectorIndex,
    database: DatabaseSettings,
    qdrant_settings: QdrantSettings,
) -> AsyncGenerator[httpx.AsyncClient]:
    await build_index(packages, vectors)
    async with running(create_app(opener(database, qdrant_settings, packages.library))) as client:
        yield client


@pytest.fixture
async def empty_site(
    database: DatabaseSettings, qdrant_settings: QdrantSettings, library: Library
) -> AsyncGenerator[httpx.AsyncClient]:
    async with running(create_app(opener(database, qdrant_settings, library))) as client:
        yield client


MCP_ACCEPT = {"accept": "application/json, text/event-stream"}
MODERN = "2026-07-28"
AGENT = {"name": "agent", "version": "1.2"}


def envelope(method: str, params: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": AGENT,
    }
    headers = {"mcp-protocol-version": MODERN, "mcp-method": method}
    name = params.get("name")
    if isinstance(name, str):
        headers["mcp-name"] = name
    return params | {"_meta": meta}, headers


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, object],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    sent, routing = envelope(method, params)
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": sent}
    return await client.post("/mcp", json=body, headers=MCP_ACCEPT | routing | (headers or {}))


async def call_tool(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, object],
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    response = await rpc(client, "tools/call", {"name": name, "arguments": arguments}, headers)
    result = response.json()["result"]
    return result["isError"], result["content"][0]["text"]


type StoredSearch = tuple[str, str | None, str | None, str, int, int, list[str]]


async def stored_searches(pool: Pool) -> list[StoredSearch]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT client, user_agent, client_name, query, page_offset, page_limit, results"
            " FROM searches ORDER BY id"
        )
        return await cursor.fetchall()


async def test_agents_get_results_with_their_code_and_import_line(
    site: httpx.AsyncClient,
) -> None:
    params = {"q": "double a number", "limit": 1}
    response = await site.get("/search.txt", params=params, headers={"accept": "text/html"})
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith(
        f"1. Kit.double ({DOUBLE_READ})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Doubles a number.\n"
    )
    code = (await site.get(DOUBLE_READ)).text
    assert code == ("3\t# Doubles a number.\n4\tdef double(n: Nat) -> Nat:\n5\t  Nat.add(n, n)\n")
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
    assert agents[:6] == ("agents", "a/1", None, "double", 0, 1)
    assert web[:6] == ("web", "b/2", None, "double a number", 0, 20)
    assert web[6][0] == DOUBLE
    assert len(web[6]) > 1


def numbered(text: str) -> str:
    return "".join(f"{number}\t{line}\n" for number, line in enumerate(text.splitlines(), 1))


async def test_agents_read_each_file_as_it_is_stored(site: httpx.AsyncClient) -> None:
    broken = await site.get("/read.txt", params={"file_path": "kit@2.0.0.0/main.bend"})
    assert broken.text == numbered(BROKEN_SOURCE)
    by_hash = await site.get("/read.txt", params={"file_path": f"{APP}/main.bend"})
    assert by_hash.text == numbered(APP_SOURCE)
    base = await site.get("/read.txt", params={"file_path": "Base/base.bend", "limit": 1})
    assert base.text == (
        "1\ttype Nat is Data:\n"
        f"More: {SITE_URL}/read.txt?file_path=Base/base.bend&limit=1&offset=2\n"
    )


async def test_agents_find_code_in_one_release(site: httpx.AsyncClient) -> None:
    content = {"pattern": "double", "path": "app@1.0.0.0", "output_mode": "content", "-n": "false"}
    assert (await site.get("/grep.txt", params=content)).text == (
        "app@1.0.0.0/main.bend:  K.double(K.double(n))\n"
    )
    files = {"pattern": "Nat", "path": "kit@1.0.0.0/main.bend"}
    assert (await site.get("/grep.txt", params=files)).text == (
        "Found 1 file\nkit@1.0.0.0/main.bend\n"
    )
    paths = {"pattern": "**/*.bend", "path": "kit@1.0.0.0"}
    assert (await site.get("/glob.txt", params=paths)).text == "kit@1.0.0.0/main.bend\n"


@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        ("/read.txt", {"file_path": "kit@9.0.0.0/main.bend"}, httpx.codes.NOT_FOUND),
        ("/read.txt", {"file_path": "kit@1.0.0.0/other.bend"}, httpx.codes.NOT_FOUND),
        ("/read.txt", {"file_path": "kit@1.0.0.0"}, httpx.codes.BAD_REQUEST),
        ("/read.txt", {"file_path": "kit@1.0.0.0/../x.bend"}, httpx.codes.BAD_REQUEST),
        ("/read.txt", {"file_path": "kit@1.0.0.0/main.bend", "offset": 9}, httpx.codes.BAD_REQUEST),
        ("/grep.txt", {"pattern": "(", "path": "kit@1.0.0.0"}, httpx.codes.BAD_REQUEST),
        ("/grep.txt", {"pattern": "x", "path": "kit@1.0.0.0/no.bend"}, httpx.codes.NOT_FOUND),
        ("/glob.txt", {"pattern": "**", "path": "nothing"}, httpx.codes.NOT_FOUND),
        ("/packages/nothing.txt", {}, httpx.codes.NOT_FOUND),
    ],
)
async def test_agents_get_a_failure_as_text(
    site: httpx.AsyncClient, path: str, params: dict[str, str | int], status: int
) -> None:
    response = await site.get(path, params=params)
    assert response.status_code == status
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
    assert f"indexed  {SITE_URL}/glob.txt?pattern=**&path=kit@1.0.0.0\n" in package
    assert f"  Kit.double ({DOUBLE_READ})\n   # Doubles a number.\n" in package
    status = (await site.get("/status.txt")).text
    assert "  Jobs: 0 queued, 0 running, 3 indexed, 0 superseded, 0 failed\n" in status
    assert f"Invalid releases (1):\n  kit@2.0.0.0 ({KIT_V2})" in status
    assert f"   At {SITE_URL}/read.txt?file_path=kit@2.0.0.0/main.bend&offset=2.\n" in status
    assert "   Files that do not load: main.bend.\n" in status


async def test_mcp_clients_get_four_read_only_tools(site: httpx.AsyncClient) -> None:
    listed = (await rpc(site, "tools/list", {})).json()["result"]
    assert [tool["name"] for tool in listed["tools"]] == ["search", "read", "grep", "glob"]
    assert all(tool["annotations"]["readOnlyHint"] for tool in listed["tools"])
    assert (listed["ttlMs"], listed["cacheScope"]) == (3_600_000, "public")
    discovered = (await rpc(site, "server/discover", {})).json()["result"]
    assert discovered["capabilities"] == {"tools": {"listChanged": False}}
    assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "JendHub",
        "version": "dev",
    }


async def test_mcp_clients_of_the_2025_protocol_also_search(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "double", "limit": 1}},
    }
    response = await site.post("/mcp", json=body, headers=MCP_ACCEPT)
    assert response.json()["result"]["content"][0]["text"].startswith("1. Kit.double (")
    [stored] = await stored_searches(pool)
    assert stored[:3] == ("mcp", "python-httpx/" + httpx.__version__, None)


async def test_mcp_search_gives_read_calls_and_is_stored(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    failed, text = await call_tool(
        site, "search", {"query": "double a number", "limit": 1}, {"user-agent": "c/3"}
    )
    assert not failed
    assert text.startswith(
        '1. Kit.double (read(file_path="kit@1.0.0.0/main.bend", offset=3, limit=3))\n'
        "   import kit@1.0.0.0/main.bend as Kit\n"
    )
    assert text.endswith('More: search(query="double a number", limit=1, offset=1)\n')
    [stored] = await stored_searches(pool)
    assert stored[:6] == ("mcp", "c/3", "agent 1.2", "double a number", 0, 1)


async def test_mcp_read_grep_and_glob_work_on_the_stored_files(site: httpx.AsyncClient) -> None:
    read: dict[str, object] = {"file_path": "app@1.0.0.0/main.bend", "offset": 2, "limit": 2}
    assert await call_tool(site, "read", read) == (
        False,
        (
            "2\timport kit@1.0.0.0/main.bend as K\n3\t\n"
            'More: read(file_path="app@1.0.0.0/main.bend", offset=4, limit=2)\n'
        ),
    )
    grep: dict[str, object] = {
        "pattern": "def",
        "path": "app@1.0.0.0",
        "output_mode": "content",
        "-A": 1,
    }
    assert await call_tool(site, "grep", grep) == (
        False,
        (
            "app@1.0.0.0/main.bend:4:def quadruple(n: Nat) -> Nat:\n"
            "app@1.0.0.0/main.bend-5-  K.double(K.double(n))\n"
        ),
    )
    assert await call_tool(site, "glob", {"pattern": "*.bend", "path": "Base"}) == (
        False,
        "Base/base.bend\n",
    )


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        (
            "read",
            {"file_path": "kit@1.0.0.0/nothing.bend"},
            "The store has no file kit@1.0.0.0/nothing.bend.",
        ),
        ("read", {"file_path": "kit/main.bend"}, "The store has no release kit."),
        (
            "grep",
            {"pattern": "x", "path": "kit@1.0.0.0", "-A": -1},
            "Invalid parameter -A: Input should be greater than or equal to 0.",
        ),
        (
            "search",
            {"query": "double", "limit": 0},
            "Invalid parameter limit: Input should be greater than or equal to 1.",
        ),
    ],
)
async def test_mcp_tools_explain_a_failure(
    site: httpx.AsyncClient, name: str, arguments: dict[str, object], message: str
) -> None:
    assert await call_tool(site, name, arguments) == (True, message)


async def test_mcp_clients_get_an_error_for_an_unknown_tool(site: httpx.AsyncClient) -> None:
    response = await rpc(site, "tools/call", {"name": "nothing", "arguments": {}})
    assert response.json()["error"]["message"] == "JendHub has no tool nothing."


async def test_browsers_get_a_search_description(site: httpx.AsyncClient) -> None:
    response = await site.get("/opensearch.xml")
    assert response.headers["content-type"].startswith("application/opensearchdescription+xml")
    assert f'template="{SITE_URL}/search?q={{searchTerms}}"' in response.text
    home = (await site.get("/")).text
    assert '<link rel="search" type="application/opensearchdescription+xml"' in home


async def test_people_get_the_results_as_a_page(site: httpx.AsyncClient) -> None:
    response = await site.get("/search", params={"q": "double a number"})
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert f'href="/definitions/{DOUBLE}"' in html
    assert 'href="/packages/kit"' in html
    assert "import kit@1.0.0.0/main.bend as Kit" in html
    assert 'type="text/plain" href="/search.txt?q=double+a+number"' in html


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
    assert 'href="/read.txt?file_path=app@1.0.0.0/main.bend&amp;offset=4&amp;limit=2"' in html


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


async def test_people_get_the_status_and_the_home_page(site: httpx.AsyncClient) -> None:
    status = (await site.get("/status")).text
    assert PRODUCTION.label in status
    assert 'href="/files/kit@2.0.0.0/main.bend#L2">main.bend:2</a>' in status
    assert "<b>2</b> packages" in (await site.get("/")).text


async def test_people_get_a_missing_page_as_html(site: httpx.AsyncClient) -> None:
    response = await site.get("/definitions/nothing")
    assert response.status_code == httpx.codes.NOT_FOUND
    assert response.headers["content-type"].startswith("text/html")
    assert "The index has no definition nothing." in response.text


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
    database: DatabaseSettings,
    qdrant_settings: QdrantSettings,
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JENDHUB_SEARCH_BURST", "1")
    monkeypatch.setenv("JENDHUB_SEARCHES_PER_MINUTE", "1")
    monkeypatch.setenv("JENDHUB_CLIENT_IP_HEADER", "cf-connecting-ip")
    async with running(create_app(opener(database, qdrant_settings, library))) as client:
        first = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
        second = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
        other = await client.get("/search.txt?q=double", headers={"cf-connecting-ip": "2.2.2.2"})
        page = await client.get("/search?q=double", headers={"cf-connecting-ip": "1.1.1.1"})
        tool = await call_tool(
            client, "search", {"query": "double"}, {"cf-connecting-ip": "1.1.1.1"}
        )
    assert tool[0]
    assert "Too many requests. Try again in " in tool[1]
    assert first.status_code == httpx.codes.OK
    assert second.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert second.text.startswith("Too many requests. Try again in ")
    assert int(second.headers["retry-after"]) > 0
    assert other.status_code == httpx.codes.OK
    assert page.status_code == httpx.codes.TOO_MANY_REQUESTS
    assert page.headers["content-type"].startswith("text/html")


@pytest.mark.usefixtures("indexed")
async def test_a_search_without_an_embedding_says_so(
    database: DatabaseSettings, qdrant_settings: QdrantSettings, library: Library
) -> None:
    app = create_app(opener(database, qdrant_settings, library, SilentEmbedder()))
    async with running(app) as client:
        response = await client.get("/search.txt", params={"q": "double"})
        tool = await call_tool(client, "search", {"query": "double"})
    assert response.status_code == httpx.codes.BAD_GATEWAY
    assert response.text == "The embedding service did not answer. Try again.\n"
    assert tool == (True, "The embedding service did not answer. Try again.")


async def test_without_a_live_index_search_waits_and_the_status_answers(
    empty_site: httpx.AsyncClient,
) -> None:
    response = await empty_site.get("/search.txt", params={"q": "double"})
    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.text == "The search index is not ready. Try again later.\n"
    assert (await empty_site.get("/readyz")).status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert (await empty_site.get("/status.txt")).text.startswith("No generation.\n")


async def test_without_a_live_index_only_base_waits(
    empty_site: httpx.AsyncClient, packages: PackageStore
) -> None:
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN_SOURCE)
    package = await empty_site.get("/read.txt", params={"file_path": "kit@2.0.0.0/main.bend"})
    assert package.text == numbered(BROKEN_SOURCE)
    base = await empty_site.get("/read.txt", params={"file_path": "Base/base.bend"})
    assert base.status_code == httpx.codes.SERVICE_UNAVAILABLE
