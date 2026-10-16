from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from jendhub.services.agents.client import SearchClient
from jendhub.services.agents.guide import ROBOTS, llms_text
from jendhub.services.agents.text import (
    Links,
    render_definition,
    render_files,
    render_package,
    render_results,
    render_status,
)
from jendhub.services.app import Running, service_app
from jendhub.services.parameters import (
    PAGE,
    Limit,
    Offset,
    PageLimit,
    QueryText,
    SearchOffset,
)
from jendhub.settings import load_settings

SEARCH_TIMEOUT = 30.0


class AgentsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    search_url: str
    agents_url: str


@dataclass(frozen=True)
class AgentsState:
    client: SearchClient
    origin: str

    def text[T](self, request: Request, found: T, render: Callable[[T, Links], str]) -> str:
        return render(found, Links(self.origin, request.url.path, dict(request.query_params)))


def file_routes(running: Running[AgentsState]) -> APIRouter:
    router = APIRouter(prefix="/files", default_response_class=PlainTextResponse)

    @router.get("/{release}")
    async def release_files(request: Request, release: str) -> str:
        state = running.get()
        found = await state.client.release_files(release)
        if found is None:
            raise HTTPException(404, f"no release {release}")
        return state.text(request, found, render_files)

    @router.get("/{release}/{path:path}")
    async def file(release: str, path: str) -> str:
        found = await running.get().client.file(release, path)
        if found is None:
            raise HTTPException(404, f"no file {release}/{path}")
        return found.text

    return router


def routes(running: Running[AgentsState]) -> APIRouter:
    router = APIRouter(default_response_class=PlainTextResponse)

    @router.get("/search")
    async def search(
        request: Request,
        q: QueryText,
        limit: Limit = 10,
        offset: SearchOffset = 0,
        user_agent: Annotated[str | None, Header()] = None,
    ) -> str:
        state = running.get()
        found = await state.client.search(q, limit, offset, user_agent)
        return state.text(request, found, render_results)

    @router.get("/definitions/{id_:path}")
    async def definition(request: Request, id_: str) -> str:
        state = running.get()
        found = await state.client.definition(id_)
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        return state.text(request, found, render_definition)

    @router.get("/packages/{name}")
    async def package(
        request: Request,
        name: str,
        definitions_offset: Offset = 0,
        definitions_limit: PageLimit = PAGE,
    ) -> str:
        state = running.get()
        found = await state.client.package(name, definitions_offset, definitions_limit)
        if found is None:
            raise HTTPException(404, f"no package {name}")
        return state.text(request, found, render_package)

    @router.get("/status")
    async def status(
        request: Request,
        invalid_releases_offset: Offset = 0,
        invalid_releases_limit: PageLimit = PAGE,
    ) -> str:
        state = running.get()
        found = await state.client.status(invalid_releases_offset, invalid_releases_limit)
        return state.text(request, found, render_status)

    @router.get("/llms.txt")
    async def llms() -> str:
        return llms_text(running.get().origin)

    @router.get("/robots.txt")
    async def robots() -> str:
        return ROBOTS

    router.include_router(file_routes(running))
    return router


def refused(error: RequestValidationError) -> str:
    problems = [f"{problem['loc'][-1]}: {problem['msg']}" for problem in error.errors()]
    return f"Invalid parameter {'; '.join(problems)}.\n"


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    running: Running[AgentsState] = Running()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        settings = load_settings(AgentsSettings)
        async with httpx.AsyncClient(
            base_url=settings.search_url, timeout=SEARCH_TIMEOUT, transport=transport
        ) as client:
            running.value = AgentsState(SearchClient(client), settings.agents_url.rstrip("/"))
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

    app = service_app("agents", lifespan, ready)

    @app.exception_handler(StarletteHTTPException)
    async def failed(_: Request, error: StarletteHTTPException) -> PlainTextResponse:
        return PlainTextResponse(f"{error.detail}\n", status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, error: RequestValidationError) -> PlainTextResponse:
        return PlainTextResponse(refused(error), status_code=400)

    app.include_router(routes(running))
    return app
