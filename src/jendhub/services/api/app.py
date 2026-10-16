from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.services.api.client import SearchClient
from jendhub.services.api.text import (
    render_definition,
    render_package,
    render_results,
    render_status,
)
from jendhub.services.app import Running, service_app
from jendhub.services.contracts import DefinitionPage, PackagePage, SearchResults, StatusPage
from jendhub.services.parameters import PAGE, Limit, Offset, PageLimit, QueryText
from jendhub.settings import load_settings

TEXT = "text/plain"
SEARCH_TIMEOUT = 30.0


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    search_url: str


def wants_text(request: Request) -> bool:
    accepted = request.headers.get("accept", "")
    first = accepted.split(",", 1)[0].split(";", 1)[0].strip()
    return first == TEXT


def answer[T: BaseModel](request: Request, found: T, render: Callable[[T], str]) -> Response:
    if wants_text(request):
        return Response(render(found), media_type=TEXT)
    return Response(found.model_dump_json(), media_type="application/json")


def v1_routes(running: Running[SearchClient]) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/search", response_model=SearchResults)
    async def search(
        request: Request,
        q: QueryText,
        limit: Limit = 10,
    ) -> Response:
        found = await running.get().search(q, limit)
        return answer(request, found, render_results)

    @router.get("/definitions/{id_:path}", response_model=DefinitionPage)
    async def definition(
        request: Request,
        id_: str,
        used_by_offset: Offset = 0,
        used_by_limit: PageLimit = PAGE,
    ) -> Response:
        found = await running.get().definition(id_, used_by_offset, used_by_limit)
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        return answer(request, found, render_definition)

    @router.get("/packages/{name}", response_model=PackagePage)
    async def package(
        request: Request,
        name: str,
        definitions_offset: Offset = 0,
        definitions_limit: PageLimit = PAGE,
    ) -> Response:
        found = await running.get().package(name, definitions_offset, definitions_limit)
        if found is None:
            raise HTTPException(404, f"no package {name}")
        return answer(request, found, render_package)

    @router.get("/status", response_model=StatusPage)
    async def status(
        request: Request,
        invalid_releases_offset: Offset = 0,
        invalid_releases_limit: PageLimit = PAGE,
    ) -> Response:
        found = await running.get().status(invalid_releases_offset, invalid_releases_limit)
        return answer(request, found, render_status)

    return router


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    running: Running[SearchClient] = Running()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        settings = load_settings(ApiSettings)
        async with httpx.AsyncClient(
            base_url=settings.search_url, timeout=SEARCH_TIMEOUT, transport=transport
        ) as client:
            running.value = SearchClient(client)
            try:
                yield
            finally:
                running.value = None

    async def ready() -> bool:
        client = running.value
        if client is None:
            return False
        try:
            response = await client.client.get("/readyz")
        except httpx.HTTPError:
            return False
        return response.is_success

    app = service_app("api", lifespan, ready)

    app.include_router(v1_routes(running))
    return app
