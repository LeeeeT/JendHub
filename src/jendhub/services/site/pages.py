import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from typing import cast
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from jendhub.indexing.status import GenerationStatus
from jendhub.search.sources import BASE, BASE_FILE

SITE = "jendhub.services.site"
STYLE = files(SITE).joinpath("static/style.css").read_text(encoding="utf-8")
EXAMPLES = ("decompress gzip data", "base64 encode", "sha256 hash of bytes", "parse json")
IMPORT = re.compile(r"^(import )([^./\s]\S*/\S+\.bend)( as .*)$")
BASE_IMPORT = re.compile(r"^(import )(Base)(\s*)$")
COUNTS: list[tuple[str, Callable[[GenerationStatus], int]]] = [
    ("Releases", lambda generation: generation.releases),
    ("Definitions", lambda generation: generation.definitions),
    ("Queued", lambda generation: generation.jobs.queued),
    ("Running", lambda generation: generation.jobs.running),
    (
        "Finished",
        lambda generation: (
            generation.jobs.done + generation.jobs.superseded + generation.jobs.invalid
        ),
    ),
    ("Failed", lambda generation: generation.jobs.failed),
]


def definition_href(id_: str) -> str:
    return f"/definitions/{quote(id_, safe='/@:')}"


def package_href(name: str) -> str:
    return f"/packages/{quote(name, safe='')}"


def release_href(release: str) -> str:
    return f"/files/{quote(release, safe='@')}"


def file_href(file: str) -> str:
    return f"/files/{quote(file, safe='/@')}"


def search_href(query: str) -> str:
    return f"/search?{urlencode({'q': query})}"


@dataclass(frozen=True)
class Link:
    before: str
    label: str
    file: str
    after: str


@dataclass(frozen=True)
class SourceLine:
    text: str
    link: Link | None


def source_line(text: str) -> SourceLine:
    if (base := BASE_IMPORT.match(text)) is not None:
        return SourceLine(text, Link(base.group(1), BASE, f"{BASE}/{BASE_FILE}", base.group(3)))
    if (found := IMPORT.match(text)) is not None:
        return SourceLine(
            text, Link(found.group(1), found.group(2), found.group(2), found.group(3))
        )
    return SourceLine(text, None)


@dataclass(frozen=True)
class Pages:
    first: int
    last: int
    total: int
    previous: str | None
    next: str | None


def with_offset(request: Request, parameter: str, offset: int) -> str:
    query = dict(request.query_params)
    query.pop(parameter, None)
    if offset:
        query[parameter] = str(offset)
    return f"{request.url.path}?{urlencode(query)}" if query else request.url.path


@dataclass(frozen=True)
class Span:
    offset: int
    shown: int
    total: int


def pages(request: Request, parameter: str, span: Span, size: int) -> Pages | None:
    offset, shown, total = span.offset, span.shown, span.total
    if total <= shown:
        return None
    previous = with_offset(request, parameter, max(0, offset - size)) if offset > 0 else None
    following = with_offset(request, parameter, offset + shown) if offset + shown < total else None
    first = offset + 1 if shown else offset
    return Pages(first, offset + shown, total, previous, following)


def environment() -> Environment:
    templates = Environment(
        loader=PackageLoader(SITE, "templates"), autoescape=select_autoescape(["html"])
    )
    shared = cast("dict[str, object]", templates.globals)
    shared.update(
        definition_href=definition_href,
        package_href=package_href,
        release_href=release_href,
        file_href=file_href,
        search_href=search_href,
    )
    shared["macros"] = templates.get_template("macros.html").module
    return templates


TEMPLATES = environment()


def page(name: str, code: int = 200, **context: object) -> HTMLResponse:
    context.setdefault("home", False)
    context.setdefault("query", "")
    context.setdefault("alternate", None)
    return HTMLResponse(TEMPLATES.get_template(name).render(context), status_code=code)
