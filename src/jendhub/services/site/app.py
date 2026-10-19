import asyncio
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException

from jendhub.errors import JendHubError
from jendhub.indexing.store import IndexStore
from jendhub.search.filters import Filters
from jendhub.search.searches import Caller
from jendhub.services.app import Running, service_app
from jendhub.services.background import every
from jendhub.services.parameters import (
    PAGE,
    Offset,
    invalid_parameters,
)
from jendhub.services.site.arguments import (
    DefinitionArguments,
    FilesArguments,
    GrepArguments,
    ReadArguments,
    SearchArguments,
)
from jendhub.services.site.guide import OPENSEARCH_TYPE, ROBOTS, llms_text, opensearch
from jendhub.services.site.limits import RateLimit, client_address
from jendhub.services.site.pages import (
    COUNTS,
    EXAMPLES,
    SCRIPT,
    STYLE,
    Span,
    marked_lines,
    page,
    pages,
    readable,
    source_lines,
)
from jendhub.services.site.text import (
    UrlLinks,
    render_definition,
    render_lines,
    render_matches,
    render_package,
    render_paths,
    render_results,
    render_status,
)
from jendhub.services.site.tools import MCP_PATH, Guard, tool_route, tool_server
from jendhub.services.site.views import (
    EMBEDDING_FAILED,
    Generations,
    LiveTarget,
    OpenResources,
    Site,
    open_resources,
)
from jendhub.settings import load_settings

WEB_RESULTS = 20
RELATIVE = UrlLinks("")
QUIET = frozenset({"/healthz", "/readyz", "/style.css", "/search.js"})
ASSET_CACHE = {"cache-control": "public, max-age=31536000, immutable"}
SEARCHES = frozenset({"/search", "/search.txt"})
QUERY = "query"
FILTERS = frozenset({"package", "uses", "used_by"})


class SiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    site_url: str
    version: str = "dev"
    client_ip_header: str | None = None
    query_cache_size: int = 1_000
    target_refresh_seconds: float = 10.0
    status_seconds: float = 30.0
    requests_per_minute: float = 600.0
    request_burst: float = 120.0
    searches_per_minute: float = 30.0
    search_burst: float = 10.0


def for_agents(path: str) -> bool:
    return path.endswith(".txt") or path == MCP_PATH


def links(site: Site, request: Request) -> UrlLinks:
    return UrlLinks(site.origin, request.url.path, tuple(request.query_params.multi_items()))


def box_text(request: Request) -> str:
    words = [
        value.strip() if name == QUERY else f"{name}={value}"
        for name, value in request.query_params.multi_items()
        if name == QUERY or name in FILTERS
    ]
    return " ".join(word for word in words if word)


def text(body: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return PlainTextResponse(body, status_code=status, headers=headers)


def failure(
    request: Request, status: int, message: str, headers: dict[str, str] | None = None
) -> Response:
    if for_agents(request.url.path):
        return text(f"{message}\n", status, headers)
    response = page(
        "error.html", code=status, status=status, phrase=HTTPStatus(status).phrase, message=message
    )
    response.headers.update(headers or {})
    return response


def guard(limit: RateLimit, client_ip_header: str | None) -> Guard:
    async def take(request: Request) -> None:
        wait = limit.take(client_address(request, client_ip_header))
        if wait:
            retry = str(math.ceil(wait))
            raise HTTPException(
                429, f"Too many requests. Try again in {retry} s.", {"retry-after": retry}
            )

    return take


def guide_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(default_response_class=PlainTextResponse, dependencies=[Depends(limited)])

    @router.get("/llms.txt")
    async def guide() -> str:
        return llms_text(running.get().origin)

    @router.get("/robots.txt")
    async def robots() -> str:
        return ROBOTS

    @router.get("/opensearch.xml")
    async def search_description() -> Response:
        return Response(opensearch(running.get().origin), media_type=OPENSEARCH_TYPE)

    return router


def agent_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(default_response_class=PlainTextResponse, dependencies=[Depends(limited)])

    @router.get("/search.txt")
    async def search(
        request: Request, background: BackgroundTasks, given: Annotated[SearchArguments, Query()]
    ) -> str:
        site = running.get()
        caller = Caller("agents", request.headers.get("user-agent"))
        found, searched = await site.search(
            given.query, given.filters, given.limit, given.offset, caller
        )
        background.add_task(site.keep, searched)
        return render_results(found, links(site, request))

    @router.get("/definition.txt")
    async def definition(request: Request, given: Annotated[DefinitionArguments, Query()]) -> str:
        site = running.get()
        return render_definition(await site.named(given), links(site, request))

    @router.get("/read.txt")
    async def read(request: Request, given: Annotated[ReadArguments, Query()]) -> str:
        site = running.get()
        return render_lines(await site.read(given), links(site, request))

    @router.get("/grep.txt")
    async def grep(request: Request, given: Annotated[GrepArguments, Query()]) -> str:
        site = running.get()
        return render_matches(await site.grep(given), links(site, request))

    @router.get("/files.txt")
    async def files(request: Request, given: Annotated[FilesArguments, Query()]) -> str:
        site = running.get()
        return render_paths(await site.files_page(given), links(site, request))

    @router.get("/packages/{name}.txt")
    async def package(request: Request, name: str, definitions_offset: Offset = 0) -> str:
        site = running.get()
        return render_package(
            await site.package(name, definitions_offset, PAGE), links(site, request)
        )

    @router.get("/status.txt")
    async def status(request: Request, invalid_releases_offset: Offset = 0) -> str:
        site = running.get()
        found = await site.status(invalid_releases_offset, PAGE)
        return render_status(found, links(site, request))

    return router


def page_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(dependencies=[Depends(limited)])

    @router.get("/")
    async def home() -> Response:
        site = running.get()
        return page(
            "home.html",
            home=True,
            examples=EXAMPLES,
            origin=site.origin,
            live=await site.overview(),
        )

    @router.get("/style.css")
    async def style() -> Response:
        return Response(STYLE, media_type="text/css", headers=ASSET_CACHE)

    @router.get("/search.js")
    async def script() -> Response:
        return Response(SCRIPT, media_type="text/javascript", headers=ASSET_CACHE)

    @router.get("/search")
    async def search(
        request: Request, background: BackgroundTasks, given: Annotated[SearchArguments, Query()]
    ) -> Response:
        text = box_text(request)
        if not text:
            return RedirectResponse("/", status_code=303)
        site = running.get()
        caller = Caller("web", request.headers.get("user-agent"))
        found, searched = await site.search(
            given.query, given.filters, WEB_RESULTS, given.offset, caller
        )
        background.add_task(site.keep, searched)
        shown = len(found.results)
        others = [pair for pair in request.query_params.multi_items() if pair[0] != "offset"]
        return page(
            "search.html",
            query=text,
            found=found,
            pages=pages(request, "offset", Span(given.offset, shown, found.total), WEB_RESULTS),
            alternate=f"/search.txt?{readable(others)}",
        )

    @router.get("/definitions/{id_:path}")
    async def definition(id_: str) -> Response:
        site = running.get()
        view = await site.definition(id_)
        key = view.detail.key
        uses, used_by = await asyncio.gather(
            site.preview(Filters(used_by=(key,))), site.preview(Filters(uses=(key,)))
        )
        return page(
            "definition.html",
            view=view,
            uses=uses,
            used_by=used_by,
            alternate=RELATIVE.definition(id_),
        )

    return router


def index_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(dependencies=[Depends(limited)])

    @router.get("/packages/{name}")
    async def package(request: Request, name: str, definitions_offset: Offset = 0) -> Response:
        view = await running.get().package(name, definitions_offset, PAGE)
        shown, total = len(view.package.definitions), view.package.definitions_total
        return page(
            "package.html",
            view=view,
            pages=pages(
                request, "definitions_offset", Span(definitions_offset, shown, total), PAGE
            ),
            alternate=f"/packages/{quote(name, safe='')}.txt",
        )

    @router.get("/files/{release}")
    async def release_files(release: str) -> Response:
        found = await running.get().files(release)
        return page("files.html", files=found, alternate=RELATIVE.files(found.release))

    @router.get("/files/{release}/{path:path}")
    async def file(release: str, path: str, lines: str | None = None) -> Response:
        view = await running.get().file(release, path)
        found = view.file
        alternate = RELATIVE.read(f"{found.release}/{found.path}")
        return page(
            "file.html",
            view=view,
            lines=source_lines(found.text, view.declared),
            marked=marked_lines(lines),
            alternate=alternate,
        )

    @router.get("/status")
    async def status(request: Request, invalid_releases_offset: Offset = 0) -> Response:
        view = await running.get().status(invalid_releases_offset, PAGE)
        return page(
            "status.html",
            view=view,
            counts=COUNTS,
            pages=pages(
                request,
                "invalid_releases_offset",
                Span(invalid_releases_offset, len(view.invalid), view.invalid_total),
                PAGE,
            ),
            alternate="/status.txt",
            section="status",
        )

    return router


def create_app(resources: OpenResources | None = None) -> FastAPI:
    settings = load_settings(SiteSettings)
    running: Running[Site] = Running()
    header = settings.client_ip_header
    requested = guard(RateLimit(settings.requests_per_minute, settings.request_burst), header)
    searching = guard(RateLimit(settings.searches_per_minute, settings.search_burst), header)
    tools = tool_server(running, searching, settings.version)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        opener = resources or (lambda: open_resources(settings.query_cache_size))
        async with opener() as opened, tools.session_manager.run():
            live = LiveTarget(IndexStore(opened.pool))
            await live.refresh()
            stop = asyncio.Event()
            follower = asyncio.create_task(
                every(settings.target_refresh_seconds, stop, live.refresh)
            )
            generations = Generations(opened.pool, settings.status_seconds)
            running.value = Site(opened, live, generations, settings.site_url.rstrip("/"))
            try:
                yield
            finally:
                running.value = None
                stop.set()
                await follower

    async def ready() -> bool:
        site = running.value
        return site is not None and site.live.current is not None

    app = service_app("site", lifespan, ready)

    @app.exception_handler(HTTPException)
    async def failed(request: Request, error: HTTPException) -> Response:
        return failure(request, error.status_code, str(error.detail), dict(error.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> Response:
        return failure(request, 400, invalid_parameters(error.errors()))

    @app.exception_handler(JendHubError)
    async def upstream(request: Request, _: JendHubError) -> Response:
        return failure(request, 502, EMBEDDING_FAILED)

    async def limited(request: Request) -> None:
        path = request.url.path
        if path in QUIET:
            return
        await requested(request)
        if path in SEARCHES:
            await searching(request)

    app.include_router(guide_routes(running, limited))
    app.include_router(agent_routes(running, limited))
    app.include_router(page_routes(running, limited))
    app.include_router(index_routes(running, limited))
    app.router.routes.append(tool_route(tools, requested))
    return app
