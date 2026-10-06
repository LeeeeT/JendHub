import argparse
import base64
import hashlib
import os
import re
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

from jend import base, embed, openrouter
from jend.corpus import Entry
from jend.index import load
from jend.score import Scorer
from jend.search import Engine, Hit, Result

MAX_QUERY_CHARS = 200
JSON_RESULTS = 20
EMBEDDING_TIMEOUT = 15.0
EXAMPLES = ("decompress gzip data", "String -> Bytes", "read a file", "parse JSON text")
HUB = "https://hub.bend-lang.com"


class Refusal(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Service:
    engine: Engine

    async def search(self, query: str) -> Result:
        if len(query) > MAX_QUERY_CHARS:
            raise Refusal(400, f"The query is longer than {MAX_QUERY_CHARS} characters.")
        try:
            return await self.engine.search(query)
        except httpx2.HTTPError as error:
            raise Refusal(502, "The embedding service did not answer. Try again.") from error


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
        "score": round(hit.score, 2),
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
<p>Searches Base and the latest versions of the 100 hottest BendHub packages. The score tells how well the definition matches the query, compared with the other results of the same query.</p>
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
        f'<li><data class="s" value="{hit.score:.4f}">{hit.score:.1f}</data><div>'
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


def create_app(data: Path | None = None) -> FastAPI:
    directory = data or Path(os.environ.get("JEND_DATA", "data/hub"))
    state: dict[str, Service] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        scorer = Scorer(load(directory))
        cache = embed.query_cache(directory)
        async with openrouter.connect(EMBEDDING_TIMEOUT) as client:
            state["service"] = Service(Engine(scorer, embed.QueryEmbedder(client, cache)))
            yield
        cache.close()

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
    async def home(q: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        query = q.strip()
        if not query:
            return page("", intro_html())
        try:
            result = await state["service"].search(query)
        except Refusal as refusal:
            return page(query, refusal_html(refusal), refusal.status)
        return page(query, results_html(result))

    @app.get("/search.json")
    async def search_json(q: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        query = q.strip()
        if not query:
            return JSONResponse({"error": "The parameter q is empty."}, status_code=400)
        try:
            result = await state["service"].search(query)
        except Refusal as refusal:
            return JSONResponse({"error": refusal.message}, status_code=refusal.status)
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
