import asyncio
import math
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException

from jendhub.errors import JendHubError
from jendhub.indexing.store import IndexStore
from jendhub.search.sources import id_source, source_id
from jendhub.services.app import Running, service_app
from jendhub.services.background import every
from jendhub.services.parameters import (
    MAX_QUERY,
    PAGE,
    Limit,
    Offset,
    QueryText,
    SearchOffset,
)
from jendhub.services.site.guide import ROBOTS, llms_text
from jendhub.services.site.limits import RateLimit, client_address
from jendhub.services.site.pages import (
    COUNTS,
    EXAMPLES,
    STYLE,
    Span,
    marked_lines,
    page,
    pages,
    source_lines,
)
from jendhub.services.site.text import (
    Links,
    render_definition,
    render_files,
    render_package,
    render_packages,
    render_results,
    render_status,
)
from jendhub.services.site.views import (
    Generations,
    LiveTarget,
    OpenResources,
    Site,
    open_resources,
)
from jendhub.settings import load_settings

WEB_RESULTS = 20
QUIET = frozenset({"/healthz", "/readyz", "/style.css"})
ASSET_CACHE = {"cache-control": "public, max-age=31536000, immutable"}
SEARCHES = frozenset({"/search", "/search.txt"})

type Guard = Callable[[Request], Awaitable[None]]


class SiteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    site_url: str
    client_ip_header: str | None = None
    query_cache_size: int = 10_000
    target_refresh_seconds: float = 10.0
    status_seconds: float = 30.0
    requests_per_minute: float = 600.0
    request_burst: float = 120.0
    searches_per_minute: float = 30.0
    search_burst: float = 10.0


def for_agents(path: str) -> bool:
    return path.endswith(".txt") or path.startswith("/src/")


def links(site: Site, request: Request) -> Links:
    return Links(site.origin, request.url.path, dict(request.query_params))


def text(body: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return PlainTextResponse(body, status_code=status, headers=headers)


def failure(
    request: Request, status: int, message: str, headers: dict[str, str] | None = None
) -> Response:
    if for_agents(request.url.path):
        return text(f"{message}\n", status, headers)
    response = page("error.html", code=status, status=status, message=message)
    response.headers.update(headers or {})
    return response


def guide_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(default_response_class=PlainTextResponse, dependencies=[Depends(limited)])

    @router.get("/llms.txt")
    async def guide() -> str:
        return llms_text(running.get().origin)

    @router.get("/robots.txt")
    async def robots() -> str:
        return ROBOTS

    return router


def agent_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(default_response_class=PlainTextResponse, dependencies=[Depends(limited)])

    @router.get("/search.txt")
    async def search(
        request: Request,
        background: BackgroundTasks,
        q: QueryText,
        limit: Limit = 10,
        offset: SearchOffset = 0,
    ) -> str:
        site = running.get()
        user_agent = request.headers.get("user-agent")
        found, searched = await site.search(q, limit, offset, "agents", user_agent)
        background.add_task(site.keep, searched)
        return render_results(found, links(site, request))

    @router.get("/src/{release}/")
    async def release_files(request: Request, release: str) -> str:
        site = running.get()
        return render_files(await site.files(release), links(site, request))

    @router.get("/src/{release}/{path:path}")
    async def source(
        request: Request,
        release: str,
        path: str,
    ) -> str:
        site = running.get()
        wanted = request.query_params.get("def")
        if wanted is None:
            return (await site.source(release, path)).text
        id_ = await source_id(site.pool, release, path, wanted)
        if id_ is None:
            raise HTTPException(404, f"The index has no definition {wanted} in {release}/{path}.")
        return render_definition(await site.definition(id_), links(site, request))

    @router.get("/packages.txt")
    async def packages(request: Request) -> str:
        site = running.get()
        return render_packages(await site.packages(), links(site, request))

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

    @router.get("/search")
    async def search(
        request: Request, background: BackgroundTasks, q: str = "", offset: SearchOffset = 0
    ) -> Response:
        query = q.strip()
        if not query:
            return RedirectResponse("/", status_code=303)
        if len(query) > MAX_QUERY:
            raise HTTPException(400, f"A query has at most {MAX_QUERY} characters.")
        site = running.get()
        user_agent = request.headers.get("user-agent")
        found, searched = await site.search(query, WEB_RESULTS, offset, "web", user_agent)
        background.add_task(site.keep, searched)
        shown = len(found.results)
        return page(
            "search.html",
            query=query,
            found=found,
            pages=pages(request, "offset", Span(offset, shown, found.total), WEB_RESULTS),
            alternate=f"/search.txt?{urlencode({'q': query})}",
        )

    @router.get("/definitions/{id_:path}")
    async def definition(id_: str) -> Response:
        view = await running.get().definition(id_)
        file, name = id_source(id_)
        alternate = f"/src/{quote(file, safe='/@')}?{urlencode({'def': name})}"
        return page("definition.html", view=view, alternate=alternate)

    return router


def index_routes(running: Running[Site], limited: Guard) -> APIRouter:
    router = APIRouter(dependencies=[Depends(limited)])

    @router.get("/packages")
    async def packages() -> Response:
        found = await running.get().packages()
        return page("packages.html", found=found, section="packages", alternate="/packages.txt")

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
            section="packages",
        )

    @router.get("/files/{release}")
    async def release_files(release: str) -> Response:
        found = await running.get().files(release)
        return page("files.html", files=found, alternate=f"/src/{quote(release, safe='@')}/")

    @router.get("/files/{release}/{path:path}")
    async def file(release: str, path: str, lines: str | None = None) -> Response:
        view = await running.get().file(release, path)
        found = view.file
        alternate = f"/src/{quote(f'{found.release}/{found.path}', safe='/@')}"
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
    requests = RateLimit(settings.requests_per_minute, settings.request_burst)
    searches = RateLimit(settings.searches_per_minute, settings.search_burst)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        opener = resources or (lambda: open_resources(settings.query_cache_size))
        async with opener() as opened:
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
        problems = [f"{problem['loc'][-1]}: {problem['msg']}" for problem in error.errors()]
        return failure(request, 400, f"Invalid parameter {'; '.join(problems)}.")

    @app.exception_handler(JendHubError)
    async def upstream(request: Request, _: JendHubError) -> Response:
        return failure(request, 502, "The embedding service did not answer. Try again.")

    async def limited(request: Request) -> None:
        path = request.url.path
        if path in QUIET:
            return
        client = client_address(request, settings.client_ip_header)
        wait = requests.take(client)
        if not wait and path in SEARCHES:
            wait = searches.take(client)
        if wait:
            retry = str(math.ceil(wait))
            raise HTTPException(
                429, f"Too many requests. Try again in {retry} s.", {"retry-after": retry}
            )

    app.include_router(guide_routes(running, limited))
    app.include_router(agent_routes(running, limited))
    app.include_router(page_routes(running, limited))
    app.include_router(index_routes(running, limited))
    return app
