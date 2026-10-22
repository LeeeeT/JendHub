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
from jendhub.search import filters
from jendhub.search.ranking import load_model
from jendhub.services.site.app import create_app
from jendhub.services.site.views import Resources
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import PRODUCTION, VectorIndex, open_vector_index
from tests.indexing.live import (
    APP,
    APP_SOURCE,
    KIT_SOURCE,
    KIT_V2,
    WordEmbedder,
    index_live,
    seed,
    store_package,
)

SITE_URL = "https://jend.test"
DOUBLE = "kit@1.0.0.0/main:double"
DOUBLE_OPEN = f"{SITE_URL}/definition.txt?path=kit@1.0.0.0/main.bend&name=double"
QUADRUPLE = "app@1.0.0.0/main:quadruple"
LONE = "0x" + "4" * 32
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


async def test_agents_get_results_with_their_definition_and_import_line(
    site: httpx.AsyncClient,
) -> None:
    params = {"query": "double a number", "limit": 1}
    response = await site.get("/search.txt", params=params, headers={"accept": "text/html"})
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith(
        f"1. Kit.double ({DOUBLE_OPEN})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Doubles a number.\n"
    )
    assert response.text.endswith(
        f"More: {SITE_URL}/search.txt?query=double+a+number&limit=1&offset=1\n"
    )


async def test_search_pages_through_one_fixed_ranking(site: httpx.AsyncClient) -> None:
    every = (await site.get("/search.txt", params={"query": "double a number", "limit": 50})).text
    later = (
        await site.get("/search.txt", params={"query": "double", "limit": 1, "offset": 1})
    ).text
    assert every.startswith("1. ")
    assert later.startswith("2. ")
    assert (await site.get("/search.txt", params={"query": "x", "offset": 50})).status_code == 400


def call_names(text: str) -> list[str]:
    return [line.split()[1] for line in text.splitlines() if line[:1].isdigit()]


async def search_text(site: httpx.AsyncClient, params: list[tuple[str, str]]) -> str:
    return (await site.get("/search.txt", params=tuple(params))).text


@pytest.mark.parametrize("largest_read", [0, filters.LARGEST_READ])
async def test_agents_filter_a_search_by_package_and_uses(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, largest_read: int
) -> None:
    monkeypatch.setattr(filters, "LARGEST_READ", largest_read)
    assert call_names(await search_text(site, [("uses", "kit/main:double")])) == ["App.quadruple"]
    assert call_names(await search_text(site, [("uses", "Nat"), ("package", "kit")])) == [
        "Kit.double"
    ]
    assert call_names(await search_text(site, [("package", "Base")])) == ["Nat", "Nat.add"]
    assert sorted(call_names(await search_text(site, [("used_by", "app/main:quadruple")]))) == [
        "Kit.double",
        "Nat",
    ]
    in_app = await search_text(site, [("query", "double a number"), ("package", "app")])
    assert call_names(in_app) == ["App.quadruple"]
    with_words = [("query", "double a number"), ("uses", "kit/main:double")]
    assert call_names(await search_text(site, with_words)) == ["App.quadruple"]
    both = [("uses", "kit/main:double"), ("uses", "Nat")]
    assert call_names(await search_text(site, both)) == ["App.quadruple"]
    assert await search_text(site, [("package", "kit"), ("package", "app")]) == "No results.\n"
    assert await search_text(site, [("uses", "kit/main:double"), ("package", "kit")]) == (
        "No results.\n"
    )


async def test_filtered_pages_keep_every_filter(site: httpx.AsyncClient) -> None:
    params = [("used_by", "app/main:quadruple"), ("package", "Base"), ("limit", "1")]
    assert call_names(await search_text(site, params)) == ["Nat"]
    assert (await search_text(site, [("used_by", "app/main:quadruple"), ("limit", "1")])).endswith(
        f"More: {SITE_URL}/search.txt?used_by=app/main:quadruple&limit=1&offset=1\n"
    )
    failed, text = await call_tool(
        site, "search", {"used_by": ["app/main:quadruple"], "uses": [], "limit": 1}
    )
    assert not failed
    assert text.endswith('More: search(used_by=["app/main:quadruple"], limit=1, offset=1)\n')


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ([], httpx.codes.BAD_REQUEST),
        ([("query", " "), ("uses", "")], httpx.codes.BAD_REQUEST),
        ([("uses", "kit/main:nothing")], httpx.codes.NOT_FOUND),
        ([("used_by", "kit@1.0.0.0/main:double")], httpx.codes.NOT_FOUND),
        ([("package", "nothing")], httpx.codes.NOT_FOUND),
    ],
)
async def test_agents_get_a_failure_for_a_wrong_filter(
    site: httpx.AsyncClient, params: list[tuple[str, str]], status: int
) -> None:
    assert (await site.get("/search.txt", params=tuple(params))).status_code == status


async def test_each_search_is_stored_with_its_client_and_whole_ranking(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    await site.get(
        "/search.txt", params={"query": "double", "limit": 1}, headers={"user-agent": "a/1"}
    )
    await site.get("/search", params={"query": "double a number"}, headers={"user-agent": "b/2"})
    [agents, web] = await stored_searches(pool)
    assert agents[:6] == ("agents", "a/1", None, "double", 0, 1)
    assert web[:6] == ("web", "b/2", None, "double a number", 0, 20)
    assert web[6][0] == DOUBLE
    assert len(web[6]) > 1


async def test_a_definition_gives_its_code_imports_lines_and_uses(site: httpx.AsyncClient) -> None:
    params = {"path": "app@1.0.0.0/main.bend", "name": "quadruple"}
    response = await site.get("/definition.txt", params=params)
    assert response.text == (
        "App.quadruple: def of app@1.0.0.0\n"
        "Import: import app@1.0.0.0/main.bend as App\n"
        f"Lines 4-5 of app@1.0.0.0/main.bend: {SITE_URL}/read.txt?path=app@1.0.0.0/main.bend"
        "&start_line=4&end_line=5\n\n"
        "import Base\nimport kit@1.0.0.0/main.bend as K\n\n"
        "def quadruple(n: Nat) -> Nat:\n  K.double(K.double(n))\n\n"
        f"Uses (2):\n  {SITE_URL}/definition.txt?path=Base/base.bend&name=Nat\n  {DOUBLE_OPEN}\n"
    )
    base = await site.get("/definition.txt", params={"path": "Base/base.bend", "name": "Nat.add"})
    assert base.text.startswith("Nat.add: def of Base\nImport: import Base\n")


def numbered(text: str, first: int = 1) -> str:
    lines = text.splitlines()[first - 1 :]
    return "".join(f"{number}\t{line}\n" for number, line in enumerate(lines, first))


async def test_agents_read_each_file_as_it_is_stored(site: httpx.AsyncClient) -> None:
    broken = await site.get("/read.txt", params={"path": "kit@2.0.0.0/main.bend"})
    assert broken.text == numbered(BROKEN_SOURCE)
    by_hash = await site.get("/read.txt", params={"path": f"{APP}/main.bend", "start_line": 2})
    assert by_hash.text == numbered(APP_SOURCE, 2)
    base = await site.get("/read.txt", params={"path": "Base/base.bend", "end_line": 1})
    assert base.text == "1\ttype Nat is Data:\n"


async def test_agents_find_code_and_files_in_one_release(site: httpx.AsyncClient) -> None:
    lines = {"pattern": "double", "path": "app@1.0.0.0"}
    assert (await site.get("/grep.txt", params=lines)).text == (
        "app@1.0.0.0/main.bend:5:  K.double(K.double(n))\n"
    )
    files = {"pattern": "Nat", "path": "kit@1.0.0.0/main.bend", "mode": "files"}
    assert (await site.get("/grep.txt", params=files)).text == "kit@1.0.0.0/main.bend\n"
    paths = {"path": "kit@1.0.0.0", "limit": 1}
    assert (await site.get("/files.txt", params=paths)).text == "kit@1.0.0.0/main.bend\n"


async def test_agents_may_leave_an_optional_value_empty(site: httpx.AsyncClient) -> None:
    read = "/read.txt?path=app@1.0.0.0/main.bend&start_line=2&end_line="
    assert (await site.get(read)).text == numbered(APP_SOURCE, 2)
    grep = (
        "/grep.txt?pattern=double&path=app@1.0.0.0&glob=&mode=&context=&before=&after="
        "&ignore_case=&multiline=&limit=&offset="
    )
    assert (await site.get(grep)).text == "app@1.0.0.0/main.bend:5:  K.double(K.double(n))\n"


@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        ("/read.txt", {"path": "kit@9.0.0.0/main.bend"}, httpx.codes.NOT_FOUND),
        ("/read.txt", {"path": "kit@1.0.0.0/other.bend"}, httpx.codes.NOT_FOUND),
        ("/read.txt", {"path": "kit@1.0.0.0"}, httpx.codes.BAD_REQUEST),
        ("/read.txt", {"path": "kit@1.0.0.0/../x.bend"}, httpx.codes.BAD_REQUEST),
        ("/read.txt", {"path": "kit@1.0.0.0/main.bend", "start_line": 9}, httpx.codes.BAD_REQUEST),
        ("/definition.txt", {"path": "kit@1.0.0.0/main.bend", "name": "no"}, httpx.codes.NOT_FOUND),
        ("/grep.txt", {"pattern": "(", "path": "kit@1.0.0.0"}, httpx.codes.BAD_REQUEST),
        ("/grep.txt", {"pattern": "x", "path": "kit@1.0.0.0/no.bend"}, httpx.codes.NOT_FOUND),
        ("/files.txt", {"path": "nothing"}, httpx.codes.NOT_FOUND),
        ("/packages/nothing.txt", {}, httpx.codes.NOT_FOUND),
    ],
)
async def test_agents_get_a_failure_as_text(
    site: httpx.AsyncClient, path: str, params: dict[str, str | int], status: int
) -> None:
    response = await site.get(path, params=params)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("params", [{"query": "map", "limit": 0}, {"query": "x" * 1001}])
async def test_agents_get_an_invalid_query_as_text(
    site: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await site.get("/search.txt", params=params)
    assert response.status_code == httpx.codes.BAD_REQUEST
    assert response.text.startswith("Invalid parameter ")


async def test_agents_get_a_package_and_the_status_as_text(site: httpx.AsyncClient) -> None:
    package = (await site.get("/packages/kit.txt")).text
    assert package.startswith("kit\n\nReleases (2):\n  2.0.0.0  ")
    assert f"indexed  {SITE_URL}/files.txt?path=kit@1.0.0.0\n" in package
    assert f"  Kit.double ({DOUBLE_OPEN})\n   # Doubles a number.\n" in package
    status = (await site.get("/status.txt")).text
    assert "  Jobs: 0 queued, 0 running, 3 indexed, 0 superseded, 0 failed\n" in status
    assert f"Invalid releases (1):\n  kit@2.0.0.0 ({KIT_V2})" in status
    assert f"   At {SITE_URL}/read.txt?path=kit@2.0.0.0/main.bend&start_line=2.\n" in status
    assert "   Files that do not load: main.bend.\n" in status


async def test_mcp_clients_get_five_read_only_tools(site: httpx.AsyncClient) -> None:
    listed = (await rpc(site, "tools/list", {})).json()["result"]
    names = [tool["name"] for tool in listed["tools"]]
    assert names == ["search", "definition", "read", "grep", "files"]
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


async def test_mcp_search_gives_definition_calls_and_is_stored(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    failed, text = await call_tool(
        site, "search", {"query": "double a number", "limit": 1}, {"user-agent": "c/3"}
    )
    assert not failed
    assert text.startswith(
        '1. Kit.double (definition(path="kit@1.0.0.0/main.bend", name="double"))\n'
        "   import kit@1.0.0.0/main.bend as Kit\n"
    )
    assert text.endswith('More: search(query="double a number", limit=1, offset=1)\n')
    [stored] = await stored_searches(pool)
    assert stored[:6] == ("mcp", "c/3", "agent 1.2", "double a number", 0, 1)


async def test_mcp_tools_open_read_and_find_code(site: httpx.AsyncClient) -> None:
    failed, opened = await call_tool(
        site, "definition", {"path": "app@1.0.0.0/main.bend", "name": "quadruple"}
    )
    assert not failed
    assert 'read(path="app@1.0.0.0/main.bend", start_line=4, end_line=5)' in opened
    assert opened.endswith('  definition(path="kit@1.0.0.0/main.bend", name="double")\n')
    read: dict[str, object] = {"path": "app@1.0.0.0/main.bend", "start_line": 2, "end_line": 3}
    assert await call_tool(site, "read", read) == (
        False,
        "2\timport kit@1.0.0.0/main.bend as K\n3\t\n",
    )
    grep: dict[str, object] = {"pattern": "def", "path": "app@1.0.0.0", "after": 1, "limit": 1}
    assert await call_tool(site, "grep", grep) == (
        False,
        (
            "app@1.0.0.0/main.bend:4:def quadruple(n: Nat) -> Nat:\n"
            'More: grep(pattern="def", path="app@1.0.0.0", after=1, limit=1, offset=1)\n'
        ),
    )
    assert await call_tool(site, "files", {"path": "Base"}) == (False, "Base/base.bend\n")


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        (
            "read",
            {"path": "kit@1.0.0.0/nothing.bend"},
            "The store has no file kit@1.0.0.0/nothing.bend.",
        ),
        (
            "read",
            {"path": "kit/main.bend"},
            (
                "The store has no release kit. The latest releases of kit are kit@2.0.0.0,"
                " kit@1.0.0.0."
            ),
        ),
        (
            "definition",
            {"path": "kit@1.0.0.0/main.bend", "name": "Kit.double"},
            "kit@1.0.0.0/main.bend has no definition Kit.double.",
        ),
        (
            "definition",
            {"path": "kit@2.0.0.0/main.bend", "name": "double"},
            "The index has no definitions of kit@2.0.0.0/main.bend.",
        ),
        (
            "grep",
            {"pattern": "x", "path": "kit@1.0.0.0", "after": -1},
            "Invalid parameter after: Input should be greater than or equal to 0.",
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
    failed, text = await call_tool(site, name, arguments)
    assert failed
    assert text.startswith(message)


async def test_mcp_clients_get_an_error_for_an_unknown_tool(site: httpx.AsyncClient) -> None:
    response = await rpc(site, "tools/call", {"name": "nothing", "arguments": {}})
    assert response.json()["error"]["message"] == "JendHub has no tool nothing."


async def test_browsers_get_a_search_description(site: httpx.AsyncClient) -> None:
    response = await site.get("/opensearch.xml")
    assert response.headers["content-type"].startswith("application/opensearchdescription+xml")
    assert f'template="{SITE_URL}/search?query={{searchTerms}}"' in response.text
    home = (await site.get("/")).text
    assert '<link rel="search" type="application/opensearchdescription+xml"' in home


NAT_LINK = '<a href="/files/Base/base.bend?lines=1-3#L1"><span class="type">Nat</span></a>'
DOUBLE_LINK = (
    '<a href="/files/kit@1.0.0.0/main.bend?lines=3-5#L3"><span class="function">K.double</span></a>'
)


async def test_people_get_the_results_as_a_page(site: httpx.AsyncClient) -> None:
    response = await site.get("/search", params={"query": "double a number"})
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert f'href="/definitions/{DOUBLE}"' in html
    assert 'href="/packages/kit"' in html
    assert "import kit@1.0.0.0/main.bend as Kit" in html
    assert '<span class="comment"># Doubles a number.</span>' in html
    assert (
        f'<span class="keyword">def</span> <a href="/definitions/{DOUBLE}">'
        f'<span class="function declared">double</span></a>(n: {NAT_LINK})' in html
    )
    assert 'type="text/plain" href="/search.txt?query=double%20a%20number"' in html


async def test_people_filter_a_search_and_see_the_filters_in_the_box(
    site: httpx.AsyncClient, pool: Pool
) -> None:
    params = [("uses", "kit/main:double"), ("query", "times four")]
    html = (await site.get("/search", params=tuple(params))).text
    assert 'value="uses=kit/main:double times four"' in html
    assert f'href="/definitions/{QUADRUPLE}"' in html
    assert 'href="/search.txt?uses=kit/main:double&amp;query=times%20four"' in html
    assert '(<a href="/search?uses=app/main:quadruple">used by 0</a>)' in html
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT query, packages, uses, used_by FROM searches")
        assert await cursor.fetchall() == [("times four", [], ["kit/main:double"], [])]
    ordered = (await site.get("/search", params=(("uses", "Nat"), ("package", "app")))).text
    assert 'value="uses=Nat package=app"' in ordered


async def test_filters_typed_in_the_box_become_parameters(site: httpx.AsyncClient) -> None:
    typed = await site.get("/search", params={"query": "times uses=kit/main:double four"})
    assert typed.status_code == httpx.codes.SEE_OTHER
    assert typed.headers["location"] == "/search?query=times%20four&uses=kit/main:double"
    first = await site.get("/search", params={"query": "package=kit double"})
    assert first.headers["location"] == "/search?package=kit&query=double"


async def test_words_like_a_filter_name_stay_words(site: httpx.AsyncClient) -> None:
    for query in ("list packages", "uses2", "used_by= x", "=kit"):
        response = await site.get("/search", params={"query": query})
        assert response.status_code == httpx.codes.OK
        assert f'value="{query}"' in response.text


async def test_an_empty_search_goes_home(site: httpx.AsyncClient) -> None:
    response = await site.get("/search", params={"query": " "})
    assert response.status_code == httpx.codes.SEE_OTHER
    assert response.headers["location"] == "/"


async def test_people_get_a_definition_with_its_lines_and_uses(site: httpx.AsyncClient) -> None:
    html = (await site.get(f"/definitions/{QUADRUPLE}")).text
    assert 'href="/files/app@1.0.0.0/main.bend#L5"' in html
    assert (
        '<span class="keyword">def</span> '
        f'<span class="function declared">quadruple</span>(n: {NAT_LINK})' in html
    )
    assert f"  {DOUBLE_LINK}({DOUBLE_LINK}(n))" in html
    assert f'href="/definitions/{DOUBLE}"' in html
    assert 'href="/files/app@1.0.0.0/main.bend?lines=4-5#L4"' in html
    assert 'href="/definition.txt?path=app@1.0.0.0/main.bend&amp;name=quadruple"' in html
    assert 'Uses <span>(<a href="/search?used_by=app/main:quadruple">2</a>)</span>' in html
    assert 'Used by <span>(<a href="/search?uses=app/main:quadruple">0</a>)</span>' in html
    assert "No other definition uses this definition." in html
    double = (await site.get(f"/definitions/{DOUBLE}")).text
    assert f'href="/definitions/{QUADRUPLE}"' in double
    assert 'Used by <span>(<a href="/search?uses=kit/main:double">1</a>)</span>' in double


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
    assert "· 5 lines · 1 definition</p>" in file
    assert 'href="/files/kit@1.0.0.0/main.bend"' in file
    assert 'href="/files/Base/base.bend"' in file
    assert (
        f'<a href="/definitions/{QUADRUPLE}"><span class="function declared">quadruple</span></a>'
        in file
    )


async def test_people_get_a_package_with_no_release_in_the_index(
    site: httpx.AsyncClient, packages: PackageStore
) -> None:
    await store_package(packages, LONE, Release("lone", "1.0.0.0"), KIT_SOURCE)
    response = await site.get("/packages/lone")
    assert response.status_code == httpx.codes.OK
    assert "No release of this package is in the index." in response.text
    assert 'class="badge unindexed"' in response.text


async def test_a_release_that_declares_nothing_is_in_the_index(
    site: httpx.AsyncClient, packages: PackageStore, vectors: VectorIndex
) -> None:
    await store_package(packages, LONE, Release("lone", "1.0.0.0"), "import Base\n")
    await index_live(packages, vectors)
    assert 'class="badge indexed"' in (await site.get("/packages/lone")).text
    params = {"path": "lone@1.0.0.0/main.bend", "name": "nothing"}
    answer = (await site.get("/definition.txt", params=params)).text
    assert "lone@1.0.0.0/main.bend has no definition nothing." in answer


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
    for path in ("/", "/search.txt?query=double", "/style.css"):
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
        first = await client.get(
            "/search.txt?query=double", headers={"cf-connecting-ip": "1.1.1.1"}
        )
        second = await client.get(
            "/search.txt?query=double", headers={"cf-connecting-ip": "1.1.1.1"}
        )
        other = await client.get(
            "/search.txt?query=double", headers={"cf-connecting-ip": "2.2.2.2"}
        )
        page = await client.get("/search?query=double", headers={"cf-connecting-ip": "1.1.1.1"})
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
        response = await client.get("/search.txt", params={"query": "double"})
        tool = await call_tool(client, "search", {"query": "double"})
    assert response.status_code == httpx.codes.BAD_GATEWAY
    assert response.text == "The embedding service did not answer. Try again.\n"
    assert tool == (True, "The embedding service did not answer. Try again.")


async def test_without_a_live_index_search_waits_and_the_status_answers(
    empty_site: httpx.AsyncClient,
) -> None:
    response = await empty_site.get("/search.txt", params={"query": "double"})
    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.text == "The search index is not ready. Try again later.\n"
    assert (await empty_site.get("/readyz")).status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert (await empty_site.get("/status.txt")).text.startswith("No generation.\n")


async def test_without_a_live_index_only_base_waits(
    empty_site: httpx.AsyncClient, packages: PackageStore
) -> None:
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN_SOURCE)
    package = await empty_site.get("/read.txt", params={"path": "kit@2.0.0.0/main.bend"})
    assert package.text == numbered(BROKEN_SOURCE)
    base = await empty_site.get("/read.txt", params={"path": "Base/base.bend"})
    assert base.status_code == httpx.codes.SERVICE_UNAVAILABLE
