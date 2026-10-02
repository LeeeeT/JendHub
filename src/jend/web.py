import argparse
import base64
import hashlib
import ipaddress
import os
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode

import httpx2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from typesafe_sdk import TypeSafeError

from jend import base, jev, openrouter
from jend.cache import Cache
from jend.corpus import Entry
from jend.index import load
from jend.limits import Budget, RateLimit, seconds_until_utc_midnight, utc_today
from jend.search import Hit, Result, Searcher

MAX_QUERY_CHARS = 200
JSON_RESULTS = 20
EMBEDDING_TIMEOUT = 15.0
JEV_TIMEOUT = 20.0
EXAMPLES = ("decompress gzip data", "String -> Bytes", "read a file", "parse JSON text")
HUB = "https://hub.bend-lang.com"


@dataclass(frozen=True)
class Settings:
    data: Path
    rate_per_minute: int
    daily_budget_usd: float
    client_ip_header: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data=Path(os.environ.get("JEND_DATA", "data/hub")),
            rate_per_minute=int(os.environ.get("JEND_RATE_PER_MINUTE", "10")),
            daily_budget_usd=float(os.environ.get("JEND_DAILY_BUDGET_USD", "1.0")),
            client_ip_header=os.environ.get("JEND_CLIENT_IP_HEADER") or None,
        )


class Refusal(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


@dataclass
class Service:
    searcher: Searcher
    rate: RateLimit
    budget: Budget

    async def search(self, query: str, client: str) -> Result:
        if len(query) > MAX_QUERY_CHARS:
            raise Refusal(400, f"The query is longer than {MAX_QUERY_CHARS} characters.")
        cached = self.searcher.lookup(query)
        if cached is not None:
            return cached
        wait = self.rate.wait(client)
        if wait > 0:
            seconds = max(1, round(wait))
            raise Refusal(429, f"Too many new queries. Try again in {seconds} s.", seconds)
        if self.budget.exhausted():
            raise Refusal(
                429,
                "The search budget for today is used. Queries that were asked before still work.",
                seconds_until_utc_midnight(),
            )
        self.rate.record(client)
        try:
            result = await self.searcher.compute(query)
        except (httpx2.HTTPError, TypeSafeError) as error:
            raise Refusal(502, "The ranking service did not answer. Try again.") from error
        self.budget.charge(result.cost)
        return result


def alias(entry: Entry) -> str:
    stem = PurePosixPath(entry.path).stem
    source = (entry.package.name or "P") if stem == "main" else stem
    words = [word for word in re.split(r"[^A-Za-z0-9]+", source) if word]
    name = "".join(word[0].upper() + word[1:] for word in words) or "P"
    return name if name[0].isalpha() else f"M{name}"


def import_line(entry: Entry) -> str | None:
    package = entry.package
    if package.is_base:
        return None
    target = package.hash if package.name is None else f"{package.name}@{package.version}"
    return f"import {target}/{entry.path} as {alias(entry)}"


def source_url(entry: Entry) -> str:
    package = entry.package
    if package.is_base:
        file = f"https://github.com/bendlang/bend/blob/{package.hash}/bend2/{base.PATH}"
    else:
        file = f"{HUB}/{package.hash}/{quote(entry.path)}"
    return f"{file}#L{entry.definition.line}"


def hit_json(hit: Hit) -> dict[str, object]:
    entry = hit.document.entry
    enrichment = hit.document.enrichment
    result: dict[str, object] = {
        "score": round(hit.probability, 2),
        "signature": entry.definition.signature,
    }
    if enrichment is not None:
        result["summary"] = enrichment.summary
    result["import"] = import_line(entry) or "import Base"
    result["source"] = source_url(entry)
    return result


STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#000}
@media (prefers-color-scheme:dark){:root{--bg:#000;--fg:#fff}}
*{box-sizing:border-box}
html{background:var(--bg);color:var(--fg);font:15px/1.6 ui-monospace,Menlo,"SF Mono",Consolas,"Liberation Mono",monospace;font-size:min(2.5vw,max(15px,1vw),26px);-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0 auto;max-width:72ch;padding:0 2ch 4em}
a{color:inherit;text-underline-offset:.2em}
a:hover{text-decoration-thickness:2px}
:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
header{display:flex;justify-content:space-between;padding:1.2em 0 0;font-size:.87em}
header nav a{margin-left:2ch}
h1{font-size:1.3em;font-weight:700;margin:3em 0 0}
form{display:flex;gap:1ch;margin:1.6em 0 0}
input{flex:1;min-width:0;font:inherit;color:var(--fg);background:var(--bg);border:2px solid var(--fg);border-radius:0;padding:.2em 1ch}
input::placeholder{color:var(--fg);opacity:.7}
button{font:inherit;font-weight:700;color:var(--bg);background:var(--fg);border:2px solid var(--fg);border-radius:0;padding:.2em 1.6ch;cursor:pointer}
p{margin:.3em 0 0}
.note{margin:1.6em 0 0}
ol{list-style:none;margin:1em 0 0;padding:0}
li{display:grid;grid-template-columns:4ch 1fr;column-gap:1.4ch;padding:1em 0;border-top:1px solid var(--fg)}
li>div{min-width:0}
.s{font-weight:700;text-align:right}
pre{margin:0;font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.sig{font-weight:700}
.m{font-size:.87em}
.i{margin:.4em 0 0;padding:.1em 1ch;font-size:.87em;border:1px solid var(--fg)}
footer{margin:3em 0 0;padding-top:1em;border-top:1px solid var(--fg);font-size:.87em}
@media (max-width:600px){html{font-size:15px}header{flex-direction:column;gap:.5em}header nav a{margin:0 2ch 0 0}}
"""
STYLE_HASH = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        f"default-src 'none'; style-src 'sha256-{STYLE_HASH}'; img-src data:;"
        " form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
}
ROBOTS = "User-agent: *\nDisallow: /?\nDisallow: /search.json\n"


def page(query: str, body: str, status: int = 200) -> HTMLResponse:
    title = f"{query} - JendHub" if query else "JendHub - search Bend definitions"
    json_link = ""
    if query:
        href = escape(f"/search.json?{urlencode({'q': query})}")
        json_link = f'<link rel="alternate" type="application/json" href="{href}">'
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="Search the definitions of Bend packages on BendHub and of Base by what they do.">
<link rel="icon" href="data:,">
{json_link}
<style>{STYLE}</style>
</head>
<body>
<!--email_off-->
<header>
<a href="/">~/jend</a>
<nav><a href="{HUB}">hub</a><a href="https://bend-lang.com">bend</a></nav>
</header>
<main>
<h1>Search Bend definitions</h1>
<form action="/" method="get" role="search">
<input name="q" value="{escape(query)}" maxlength="{MAX_QUERY_CHARS}" placeholder="what the definition does, a name, or a type" aria-label="query" spellcheck="false" autocomplete="off"{"" if query else " autofocus"}>
<button>find</button>
</form>
{body}
</main>
<footer>
<p>Searches Base and the latest versions of the 50 hottest BendHub packages. The score is the probability, judged by Jev, that a programmer would call the definition to do what the query asks.</p>
<p>For programs and LLMs: <code>GET /search.json?q=…</code> returns the best {JSON_RESULTS} results as JSON: score, signature, summary, import line and source URL.</p>
</footer>
<!--/email_off-->
</body>
</html>
"""
    return HTMLResponse(html, status_code=status)


def results_html(result: Result) -> str:
    rows = "\n".join(hit_html(hit) for hit in result.ranking)
    return (
        f'<p class="note">{len(result.ranking)} definitions for “{escape(result.query)}”,'
        f" best first.</p>\n<ol>\n{rows}\n</ol>"
    )


def hit_html(hit: Hit) -> str:
    entry = hit.document.entry
    enrichment = hit.document.enrichment
    summary = "" if enrichment is None else f"<p>{escape(enrichment.summary)}</p>"
    line = import_line(entry)
    usage = (
        '<p class="m">in Base, no import needed</p>'
        if line is None
        else f'<pre class="i"><code>{escape(line)}</code></pre>'
    )
    return (
        f'<li><data class="s" value="{hit.probability:.4f}">{hit.probability:.2f}</data><div>'
        f'<pre class="sig"><code>{escape(entry.definition.signature)}</code></pre>'
        f"{summary}"
        f'<p class="m"><a href="{escape(source_url(entry))}">{escape(entry.package_label)}'
        f"/{escape(entry.path)}</a> line {entry.definition.line}</p>"
        f"{usage}</div></li>"
    )


def intro_html() -> str:
    links = ", ".join(
        f'<a href="/?{escape(urlencode({"q": example}))}">{escape(example)}</a>'
        for example in EXAMPLES
    )
    return f'<p class="note">Describe what you need in plain words. Try {links}.</p>'


def refusal_html(refusal: Refusal) -> str:
    return f'<p class="note"><strong>{escape(refusal.message)}</strong></p>'


def client_key(request: Request, header: str | None) -> str:
    address = request.headers.get(header) if header is not None else None
    if address is None:
        address = request.client.host if request.client is not None else "unknown"
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _retry_headers(refusal: Refusal) -> dict[str, str]:
    return {} if refusal.retry_after is None else {"Retry-After": str(refusal.retry_after)}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Service] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        index = load(settings.data)
        cache = Cache(settings.data / "index" / "cache.sqlite")
        budget = Budget(
            settings.data / "index" / "budget.sqlite", settings.daily_budget_usd, utc_today
        )
        async with (
            openrouter.connect(EMBEDDING_TIMEOUT) as embedder,
            jev.connect(JEV_TIMEOUT) as judge,
        ):
            state["service"] = Service(
                Searcher(index, cache, embedder, judge),
                RateLimit(settings.rate_per_minute, time.monotonic),
                budget,
            )
            yield
        cache.close()
        budget.close()

    app = FastAPI(title="JendHub", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def add_security_headers(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots() -> str:  # pyright: ignore[reportUnusedFunction]
        return ROBOTS

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, q: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        query = q.strip()
        if not query:
            return page("", intro_html())
        try:
            result = await state["service"].search(
                query, client_key(request, settings.client_ip_header)
            )
        except Refusal as refusal:
            response = page(query, refusal_html(refusal), refusal.status)
            response.headers.update(_retry_headers(refusal))
            return response
        return page(query, results_html(result))

    @app.get("/search.json")
    async def search_json(request: Request, q: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        query = q.strip()
        if not query:
            return JSONResponse({"error": "The parameter q is empty."}, status_code=400)
        try:
            result = await state["service"].search(
                query, client_key(request, settings.client_ip_header)
            )
        except Refusal as refusal:
            return JSONResponse(
                {"error": refusal.message, "retry_after": refusal.retry_after},
                status_code=refusal.status,
                headers=_retry_headers(refusal),
            )
        return JSONResponse(
            {
                "query": result.query,
                "results": [hit_json(hit) for hit in result.ranking[:JSON_RESULTS]],
            }
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the JendHub search page and JSON API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        create_app(), host=args.host, port=args.port, proxy_headers=False, server_header=False
    )


if __name__ == "__main__":
    main()
