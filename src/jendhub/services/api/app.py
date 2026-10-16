from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.services.api.client import SearchClient
from jendhub.services.api.text import render_definition, render_results
from jendhub.services.app import Running, service_app
from jendhub.services.contracts import DefinitionPage, SearchResults
from jendhub.settings import load_settings

MAX_QUERY = 1000
MAX_LIMIT = 50
MAX_PAGE = 500
TEXT = "text/plain"
SEARCH_TIMEOUT = 30.0


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    search_url: str


def wants_text(request: Request) -> bool:
    accepted = request.headers.get("accept", "")
    first = accepted.split(",", 1)[0].split(";", 1)[0].strip()
    return first == TEXT


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = load_settings(ApiSettings)
    running: Running[SearchClient] = Running()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
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

    @app.get("/v1/search", response_model=SearchResults)
    async def search(
        request: Request,
        q: str = Query(min_length=1, max_length=MAX_QUERY),
        limit: int = Query(10, ge=1, le=MAX_LIMIT),
    ) -> Response:
        found = await running.get().search(q, limit)
        if wants_text(request):
            return Response(render_results(found), media_type=TEXT)
        return Response(found.model_dump_json(), media_type="application/json")

    @app.get("/v1/definitions/{id_:path}", response_model=DefinitionPage)
    async def definition(
        request: Request,
        id_: str,
        used_by_offset: int = Query(0, ge=0),
        used_by_limit: int = Query(100, ge=1, le=MAX_PAGE),
    ) -> Response:
        found = await running.get().definition(id_, used_by_offset, used_by_limit)
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        if wants_text(request):
            return Response(render_definition(found), media_type=TEXT)
        return Response(found.model_dump_json(), media_type="application/json")

    return app
