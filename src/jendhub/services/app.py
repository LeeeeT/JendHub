import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

REQUEST_SECONDS = Histogram(
    "jendhub_http_request_seconds",
    "The time to answer an HTTP request.",
    ["service", "route", "status"],
)

type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]
type Readiness = Callable[[], Awaitable[bool]]
type Endpoint = Callable[[Request], Awaitable[Response]]


@dataclass
class Running[T]:
    value: T | None = field(default=None)

    def get(self) -> T:
        if self.value is None:
            raise HTTPException(503, "the service is starting or stopping")
        return self.value


def service_app(name: str, lifespan: Lifespan, ready: Readiness) -> FastAPI:
    app = FastAPI(title=f"JendHub {name}", lifespan=lifespan)

    @app.middleware("http")
    async def measure(request: Request, call_next: Endpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unknown")
        REQUEST_SECONDS.labels(name, path, str(response.status_code)).observe(
            time.perf_counter() - start
        )
        return response

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        if await ready():
            return Response("ready\n", media_type="text/plain")
        return Response("not ready\n", status_code=503, media_type="text/plain")

    return app
