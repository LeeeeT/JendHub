import hashlib
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
from jendhub.search.sources import BASE, BASE_FILE, Declared, id_source

SITE = "jendhub.services.site"
STYLE = files(SITE).joinpath("static/style.css").read_text(encoding="utf-8")
ASSETS = hashlib.sha256(STYLE.encode()).hexdigest()[:12]
EXAMPLES = ("decompress gzip data", "base64 encode", "sha256 hash of bytes", "parse json")
IMPORT = re.compile(r"^(import )([^./\s]\S*/\S+\.bend)( as .*)$")
BASE_IMPORT = re.compile(r"^(import )(Base)(\s*)$")
HEAD_LINES = 12
COUNTS: list[tuple[str, Callable[[GenerationStatus], int]]] = [
    ("Releases", lambda generation: generation.releases),
    ("Definitions", lambda generation: generation.definitions),
    ("Queued", lambda generation: generation.jobs.queued),
    ("Running", lambda generation: generation.jobs.running),
    ("Indexed", lambda generation: generation.jobs.done),
    ("Superseded", lambda generation: generation.jobs.superseded),
    ("Invalid", lambda generation: generation.jobs.invalid),
    ("Failed", lambda generation: generation.jobs.failed),
]


def counted(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def definition_href(id_: str) -> str:
    return f"/definitions/{quote(id_, safe='/@:')}"


def package_href(name: str) -> str:
    return f"/packages/{quote(name, safe='')}"


def release_href(release: str) -> str:
    return f"/files/{quote(release, safe='@')}"


def file_href(file: str) -> str:
    return f"/files/{quote(file, safe='/@')}"


def lines_href(file: str, first: int, last: int) -> str:
    if first == last:
        return f"{file_href(file)}#L{first}"
    return f"{file_href(file)}?{urlencode({'lines': f'{first}-{last}'})}#L{first}"


LINES = re.compile(r"^(\d+)-(\d+)$")


def marked_lines(lines: str | None) -> range:
    found = None if lines is None else LINES.match(lines)
    if found is None:
        return range(0)
    first, last = sorted((int(found.group(1)), int(found.group(2))))
    return range(first, last + 1)


def search_href(query: str) -> str:
    return f"/search?{urlencode({'q': query})}"


@dataclass(frozen=True)
class Reference:
    id: str
    file: str
    name: str

    @property
    def where(self) -> str:
        return BASE if self.file == f"{BASE}/{BASE_FILE}" else self.file.removesuffix(".bend")


def reference(id_: str) -> Reference:
    file, name = id_source(id_)
    return Reference(id_, file, name)


@dataclass(frozen=True)
class References:
    file: str
    where: str
    found: list[Reference]


def by_file(ids: list[str]) -> list[References]:
    grouped: dict[str, list[Reference]] = {}
    for id_ in ids:
        found = reference(id_)
        grouped.setdefault(found.file, []).append(found)
    files = sorted(grouped, key=lambda file: (file != f"{BASE}/{BASE_FILE}", file))
    return [References(file, grouped[file][0].where, grouped[file]) for file in files]


def shortened(head: str) -> str:
    lines = head.split("\n")
    return head if len(lines) <= HEAD_LINES else "\n".join([*lines[: HEAD_LINES - 1], "  …"])


@dataclass(frozen=True)
class Link:
    before: str
    label: str
    href: str
    after: str


@dataclass(frozen=True)
class SourceLine:
    text: str
    link: Link | None


def declaration_link(text: str, declared: Declared) -> Link | None:
    found = re.search(rf"(?<![\w.]){re.escape(declared.name)}(?![\w.])", text)
    if found is None:
        return None
    return Link(
        text[: found.start()], declared.name, definition_href(declared.id), text[found.end() :]
    )


def source_line(text: str, declared: Declared | None = None) -> SourceLine:
    if (base := BASE_IMPORT.match(text)) is not None:
        href = file_href(f"{BASE}/{BASE_FILE}")
        return SourceLine(text, Link(base.group(1), BASE, href, base.group(3)))
    if (found := IMPORT.match(text)) is not None:
        href = file_href(found.group(2))
        return SourceLine(text, Link(found.group(1), found.group(2), href, found.group(3)))
    if declared is not None:
        return SourceLine(text, declaration_link(text, declared))
    return SourceLine(text, None)


def source_lines(text: str, declared: list[Declared]) -> list[SourceLine]:
    by_line = {found.line: found for found in declared}
    return [
        source_line(line, by_line.get(number))
        for number, line in enumerate(text.removesuffix("\n").split("\n"), start=1)
    ]


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
        loader=PackageLoader(SITE, "templates"),
        autoescape=select_autoescape(["html"]),
    )
    shared = cast("dict[str, object]", templates.globals)
    shared.update(
        definition_href=definition_href,
        package_href=package_href,
        release_href=release_href,
        file_href=file_href,
        lines_href=lines_href,
        search_href=search_href,
        reference=reference,
        by_file=by_file,
        source_line=source_line,
        shortened=shortened,
        counted=counted,
        assets=ASSETS,
    )
    shared["macros"] = templates.get_template("macros.html").module
    return templates


TEMPLATES = environment()


def page(name: str, code: int = 200, **context: object) -> HTMLResponse:
    context.setdefault("home", False)
    context.setdefault("query", "")
    context.setdefault("alternate", None)
    context.setdefault("section", None)
    return HTMLResponse(TEMPLATES.get_template(name).render(context), status_code=code)
