from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.services.api.client import SearchClient
from jendhub.services.api.guide import ROBOTS, llms_text
from jendhub.services.api.text import (
    Links,
    render_definition,
    render_files,
    render_package,
    render_results,
    render_status,
)
from jendhub.services.app import Running, service_app
from jendhub.services.contracts import (
    DefinitionPage,
    FileList,
    FileText,
    PackagePage,
    SearchResults,
    StatusPage,
)
from jendhub.services.parameters import (
    PAGE,
    Limit,
    Offset,
    PageLimit,
    QueryText,
    SearchOffset,
)
from jendhub.settings import load_settings

TEXT = "text/plain"
SEARCH_TIMEOUT = 30.0


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    search_url: str
    public_api_url: str


@dataclass(frozen=True)
class ApiState:
    client: SearchClient
    origin: str


def wants_text(request: Request) -> bool:
    accepted = request.headers.get("accept", "")
    first = accepted.split(",", 1)[0].split(";", 1)[0].strip()
    return first == TEXT


def answer[T: BaseModel](
    request: Request, origin: str, found: T, render: Callable[[T, Links], str]
) -> Response:
    if wants_text(request):
        links = Links(origin, request.url.path, dict(request.query_params))
        return Response(render(found, links), media_type=TEXT)
    return Response(found.model_dump_json(), media_type="application/json")


def file_routes(running: Running[ApiState]) -> APIRouter:
    router = APIRouter(prefix="/files")

    @router.get("/{release}", response_model=FileList)
    async def release_files(request: Request, release: str) -> Response:
        state = running.get()
        found = await state.client.release_files(release)
        if found is None:
            raise HTTPException(404, f"no release {release}")
        return answer(request, state.origin, found, render_files)

    @router.get("/{release}/{path:path}", response_model=FileText)
    async def file(request: Request, release: str, path: str) -> Response:
        found = await running.get().client.file(release, path)
        if found is None:
            raise HTTPException(404, f"no file {release}/{path}")
        if wants_text(request):
            return Response(found.text, media_type=TEXT)
        return Response(found.model_dump_json(), media_type="application/json")

    return router


def routes(running: Running[ApiState]) -> APIRouter:
    router = APIRouter()

    @router.get("/search", response_model=SearchResults)
    async def search(
        request: Request,
        q: QueryText,
        limit: Limit = 10,
        offset: SearchOffset = 0,
    ) -> Response:
        state = running.get()
        found = await state.client.search(q, limit, offset)
        return answer(request, state.origin, found, render_results)

    @router.get("/definitions/{id_:path}", response_model=DefinitionPage)
    async def definition(
        request: Request,
        id_: str,
        used_by_offset: Offset = 0,
        used_by_limit: PageLimit = PAGE,
    ) -> Response:
        state = running.get()
        found = await state.client.definition(id_, used_by_offset, used_by_limit)
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        return answer(request, state.origin, found, render_definition)

    @router.get("/packages/{name}", response_model=PackagePage)
    async def package(
        request: Request,
        name: str,
        definitions_offset: Offset = 0,
        definitions_limit: PageLimit = PAGE,
    ) -> Response:
        state = running.get()
        found = await state.client.package(name, definitions_offset, definitions_limit)
        if found is None:
            raise HTTPException(404, f"no package {name}")
        return answer(request, state.origin, found, render_package)

    @router.get("/status", response_model=StatusPage)
    async def status(
        request: Request,
        invalid_releases_offset: Offset = 0,
        invalid_releases_limit: PageLimit = PAGE,
    ) -> Response:
        state = running.get()
        found = await state.client.status(invalid_releases_offset, invalid_releases_limit)
        return answer(request, state.origin, found, render_status)

    router.include_router(file_routes(running))
    return router


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    running: Running[ApiState] = Running()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        settings = load_settings(ApiSettings)
        async with httpx.AsyncClient(
            base_url=settings.search_url, timeout=SEARCH_TIMEOUT, transport=transport
        ) as client:
            running.value = ApiState(SearchClient(client), settings.public_api_url.rstrip("/"))
            try:
                yield
            finally:
                running.value = None

    async def ready() -> bool:
        state = running.value
        if state is None:
            return False
        try:
            response = await state.client.client.get("/readyz")
        except httpx.HTTPError:
            return False
        return response.is_success

    app = service_app("api", lifespan, ready)

    @app.get("/llms.txt")
    async def llms() -> Response:
        return Response(llms_text(running.get().origin), media_type=TEXT)

    @app.get("/robots.txt")
    async def robots() -> Response:
        return Response(ROBOTS, media_type=TEXT)

    app.include_router(routes(running))
    return app
