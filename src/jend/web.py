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
from typing import Annotated
from urllib.parse import quote, urlencode

import httpx2
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from jend import base, embed, openrouter
from jend.index import INDEX, Index, Record
from jend.score import Scorer
from jend.search import PAGE, Engine, Hit, Result

MAX_QUERY_CHARS = 200
TEXT_RESULTS = 20
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

    async def search(self, query: str, start: int, count: int) -> Result:
        if len(query) > MAX_QUERY_CHARS:
            raise Refusal(400, f"The query is longer than {MAX_QUERY_CHARS} characters.")
        try:
            return await self.engine.search(query, start, count)
        except httpx2.HTTPError as error:
            raise Refusal(502, "The embedding service did not answer. Try again.") from error


def alias(record: Record) -> str:
    stem = PurePosixPath(record.path).stem
    source = (record.package_name or "P") if stem == "main" else stem
    words = [word for word in re.split(r"[^A-Za-z0-9]+", source) if word]
    name = "".join(word[0].upper() + word[1:] for word in words) or "P"
    return name if name[0].isalpha() else f"M{name}"


def import_line(record: Record) -> str:
    if record.is_base:
        return "import Base"
    return f"import {record.package_label}/{record.path} as {alias(record)}"


def call_name(record: Record) -> str:
    return record.name if record.is_base else f"{alias(record)}.{record.name}"


def source_url(record: Record) -> str:
    if record.is_base:
        file = f"https://github.com/bendlang/bend/blob/{record.package_hash}/bend2/{base.PATH}"
    else:
        file = f"{HUB}/{record.package_hash}/{quote(record.path)}"
    return f"{file}#L{record.line}"


def results_text(result: Result) -> str:
    groups: dict[str, list[str]] = {}
    for rank, hit in enumerate(result.hits, 1):
        groups.setdefault(import_line(hit.record), []).append(hit_text(rank, hit))
    heading = (
        f"Bend definitions for “{result.query}”: the best {len(result.hits)} of"
        f" {result.total}. A higher score is a better match."
    )
    return "\n\n".join([heading, *("\n\n".join([line, *hits]) for line, hits in groups.items())])


def hit_text(rank: int, hit: Hit) -> str:
    record = hit.record
    lines = [f"{rank}. {call_name(record)} (score {hit.score:.2f})", *record.signature.splitlines()]
    if record.doc is not None:
        lines.append(f"doc: {record.doc}")
    if record.summary is not None:
        lines.append(f"summary: {record.summary}")
    lines.append(f"source: {source_url(record)}")
    return "\n   ".join(lines)


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
SCRIPT = """
const list = document.querySelector("ol[data-total]");
const watch = new IntersectionObserver(async ([entry]) => {
  if (!entry.isIntersecting) return;
  watch.disconnect();
  const shown = list.children.length;
  const response = await fetch("/more?" + new URLSearchParams({q: list.dataset.query, start: shown}));
  if (!response.ok) return;
  list.insertAdjacentHTML("beforeend", await response.text());
  follow(shown);
}, {rootMargin: "0px 0px 100% 0px"});
function follow(shown) {
  const count = list.children.length;
  if (count > shown && count < Number(list.dataset.total)) watch.observe(list.lastElementChild);
}
if (list) follow(0);
"""


def digest(source: str) -> str:
    return base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        f"default-src 'none'; style-src 'sha256-{digest(STYLE)}';"
        f" script-src 'sha256-{digest(SCRIPT)}'; connect-src 'self'; img-src data:;"
        " form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
}
ROBOTS = "User-agent: *\nDisallow: /?\nDisallow: /more\n"
LLMS = f"""# JendHub

> A search engine for Bend definitions. It searches Base and the latest
> versions of the 100 hottest BendHub packages ({HUB}).
> A query can tell what the definition does, give a name, or give a type.

## Search

GET /search.txt?q=<query>

The answer is plain text with the best {TEXT_RESULTS} definitions. Results that need the
same import line are under that line. Each result gives its rank, the name to
use in your code, its score, its declaration, the doc comment of its author when
it has one, a summary, and the URL of its source. A higher score is a better
match. You cannot compare the scores of two queries. A query has at most
{MAX_QUERY_CHARS} characters.

## Use a result in Bend

Write the import line at the top of your file. Then use the name of the result:
the alias of the import, a dot, and the name of the definition. For example,
after `import bend-kit-zlib@0.2.0.0/zlib.bend as Zlib`, call `Zlib.gunzip(data)`.
Definitions of Base need `import Base`, and you use their names without an
alias, for example `String.eq(a, b)`.
"""


def page(query: str, body: str, status: int = 200) -> HTMLResponse:
    title = f"{query} - JendHub" if query else "JendHub - search Bend definitions"
    text_link = ""
    if query:
        href = escape(f"/search.txt?{urlencode({'q': query})}")
        text_link = f'<link rel="alternate" type="text/plain" href="{href}">'
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="Search the definitions of Bend packages on BendHub and of Base by what they do.">
<link rel="icon" href="data:,">
{text_link}
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
<p>For LLMs: <code>GET /search.txt?q=…</code> returns the best {TEXT_RESULTS} results as plain text: the name to use, score, declaration, doc comment, summary, import line and source URL. <a href="/llms.txt">/llms.txt</a> tells how to use it.</p>
</footer>
<!--/email_off-->
<script type="module">{SCRIPT}</script>
</body>
</html>
"""
    return HTMLResponse(html, status_code=status)


def results_html(result: Result) -> str:
    return (
        f'<p class="note">{result.total} definitions for “{escape(result.query)}”,'
        f' best first.</p>\n<ol data-query="{escape(result.query)}" data-total="{result.total}">'
        f"\n{hits_html(result)}\n</ol>"
    )


def hits_html(result: Result) -> str:
    return "\n".join(hit_html(hit) for hit in result.hits)


def hit_html(hit: Hit) -> str:
    record = hit.record
    summary = "" if record.summary is None else f"<p>{escape(record.summary)}</p>"
    return (
        f'<li><data class="s" value="{hit.score:.4f}">{hit.score:.1f}</data><div>'
        f'<pre class="sig"><code>{escape(record.signature)}</code></pre>'
        f"{summary}"
        f'<p class="m"><a href="{escape(source_url(record))}">{escape(record.package_label)}'
        f"/{escape(record.path)}</a> line {record.line}</p>"
        f'<pre class="i"><code>{escape(import_line(record))}</code></pre></div></li>'
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
        index = Index(directory / INDEX)
        cache = embed.query_cache(directory)
        async with openrouter.connect(EMBEDDING_TIMEOUT) as client:
            state["service"] = Service(Engine(Scorer(index), embed.QueryEmbedder(client, cache)))
            yield
        cache.close()
        index.close()

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
            result = await state["service"].search(query, 0, PAGE)
        except Refusal as refusal:
            return page(query, refusal_html(refusal), refusal.status)
        return page(query, results_html(result))

    @app.get("/more", response_class=HTMLResponse)
    async def more(  # pyright: ignore[reportUnusedFunction]
        q: str, start: Annotated[int, Query(ge=0)]
    ) -> Response:
        query = q.strip()
        if not query:
            return PlainTextResponse("The parameter q is empty.", status_code=400)
        try:
            result = await state["service"].search(query, start, PAGE)
        except Refusal as refusal:
            return PlainTextResponse(refusal.message, status_code=refusal.status)
        return HTMLResponse(hits_html(result))

    @app.get("/llms.txt", response_class=PlainTextResponse)
    async def llms() -> str:  # pyright: ignore[reportUnusedFunction]
        return LLMS

    @app.get("/search.txt", response_class=PlainTextResponse)
    async def search_text(q: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        query = q.strip()
        if not query:
            return PlainTextResponse("The parameter q is empty.", status_code=400)
        try:
            result = await state["service"].search(query, 0, TEXT_RESULTS)
        except Refusal as refusal:
            return PlainTextResponse(refusal.message, status_code=refusal.status)
        return PlainTextResponse(results_text(result))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the JendHub search page and the text API for LLMs."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        create_app(), host=args.host, port=args.port, proxy_headers=False, server_header=False
    )


if __name__ == "__main__":
    main()
