import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from importlib.resources import files
from itertools import pairwise
from typing import cast
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from jendhub.indexing.status import GenerationStatus
from jendhub.search.definitions import DefinitionDetail
from jendhub.search.highlights import Extent, Highlight, LineHighlights
from jendhub.search.sources import BASE, BASE_FILE, SourceFile, id_source

SITE = "jendhub.services.site"
STYLE = files(SITE).joinpath("static/style.css").read_text(encoding="utf-8")
SCRIPT = files(SITE).joinpath("static/search.js").read_text(encoding="utf-8")
ASSETS = hashlib.sha256(f"{STYLE}{SCRIPT}".encode()).hexdigest()[:12]
EXAMPLES = ("decompress gzip data", "base64 encode", "sha256 hash of bytes", "parse json")
IMPORT = re.compile(r"^(import )([^./\s]\S*/\S+\.bend)( as .*)$")
BASE_IMPORT = re.compile(r"^(import )(Base)(\s*)$")
HEAD_LINES = 12
CUT = "  …"
DECLARED = frozenset({"function", "type", "constructor", "namespace"})
STYLED = frozenset({"keyword", "comment", "string", "number", "function", "type", "constructor"})
COUNTS: list[tuple[str, Callable[[GenerationStatus], int]]] = [
    ("Releases", lambda generation: generation.releases),
    ("Definitions", lambda generation: generation.definitions),
    ("Queued", lambda generation: generation.jobs.queued),
    ("Running", lambda generation: generation.jobs.running),
    ("Indexed", lambda generation: generation.jobs.done),
    ("Superseded", lambda generation: generation.jobs.superseded),
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


def readable(query: Sequence[tuple[str, str]]) -> str:
    return urlencode(query, safe="/:@", quote_via=quote)


def search_href(query: str) -> str:
    return f"/search?{readable([('query', query)])}"


def filter_href(name: str, value: str) -> str:
    return f"/search?{readable([(name, value)])}"


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


def shortened(head: str) -> str:
    lines = head.split("\n")
    return head if len(lines) <= HEAD_LINES else "\n".join([*lines[: HEAD_LINES - 1], CUT])


@dataclass(frozen=True)
class Link:
    before: str
    label: str
    href: str
    after: str


@dataclass(frozen=True)
class Piece:
    text: str
    style: str | None


@dataclass(frozen=True)
class Run:
    pieces: list[Piece]
    href: str | None = None


@dataclass(frozen=True)
class SourceLine:
    runs: list[Run]


@dataclass(frozen=True)
class Links:
    extents: dict[str, Extent]
    declarations: bool

    def href(self, found: Highlight[str]) -> str | None:
        if found.target is None or (extent := self.extents.get(found.target)) is None:
            return None
        if found.declaration:
            return definition_href(found.target) if self.declarations else None
        file, _ = id_source(found.target)
        return lines_href(file, extent.first_line + 1, extent.end_line)


def style(found: Highlight[str]) -> str | None:
    if found.declaration and found.kind in DECLARED:
        return f"{found.kind} declared"
    return found.kind if found.kind in STYLED else None


def qualified(highlights: list[Highlight[str]]) -> list[Highlight[str]]:
    return [
        replace(found, kind=after.kind, target=after.target)
        if found.kind == "namespace"
        and not found.declaration
        and after is not None
        and after.start == found.end + 1
        else found
        for found, after in pairwise([*highlights, None])
        if found is not None
    ]


def joined(runs: list[Run], piece: Piece, href: str | None) -> None:
    if runs and runs[-1].href == href:
        runs[-1] = Run([*runs[-1].pieces, piece], href)
    else:
        runs.append(Run([piece], href))


def styled(text: str, highlights: list[Highlight[str]], links: Links, start: int = 0) -> list[Run]:
    runs: list[Run] = []
    done = 0
    for found in qualified(highlights):
        name = style(found)
        href = links.href(found)
        first = max(found.start - start, done)
        last = min(found.end - start, len(text))
        if (name is None and href is None) or first >= last:
            continue
        gap = text[done:first]
        if gap == "." and runs and runs[-1].href == href and runs[-1].pieces[-1].style == name:
            *pieces, piece = runs[-1].pieces
            runs[-1] = Run([*pieces, Piece(piece.text + text[done:last], name)], href)
        else:
            if gap:
                joined(runs, Piece(gap, None), None)
            joined(runs, Piece(text[first:last], name), href)
        done = last
    if done < len(text):
        joined(runs, Piece(text[done:], None), None)
    return runs


def import_link(text: str) -> Link | None:
    if (base := BASE_IMPORT.match(text)) is not None:
        return Link(base.group(1), BASE, file_href(f"{BASE}/{BASE_FILE}"), base.group(3))
    if (found := IMPORT.match(text)) is not None:
        return Link(found.group(1), found.group(2), file_href(found.group(2)), found.group(3))
    return None


def styled_as(text: str, highlights: list[Highlight[str]], start: int) -> list[Piece]:
    found = next((found for found in highlights if found.start == start), None)
    return [Piece(text, None if found is None else style(found))] if text else []


def source_line(
    text: str, highlights: list[Highlight[str]], links: Links, shown: str | None = None
) -> SourceLine:
    shown = text if shown is None else shown
    if (link := import_link(shown)) is None:
        return SourceLine(styled(shown, highlights if shown == text else [], links))
    after = len(text) - len(link.after)
    if not (text.startswith(link.before) and text.endswith(link.after)):
        highlights = []
    return SourceLine(
        [
            *styled(link.before, highlights, links),
            Run(styled_as(link.label, highlights, len(link.before)), link.href),
            *styled(link.after, highlights, links, after),
        ]
    )


def file_lines(
    file: SourceFile, highlights: LineHighlights | None, links: Links
) -> list[SourceLine]:
    lines = file.text.removesuffix("\n").split("\n") if file.text else []
    found = highlights if highlights is not None and len(highlights) == len(lines) else None
    return [
        source_line(line, [] if found is None else found[index], links, file.shown(line))
        for index, line in enumerate(lines)
    ]


def import_lines(
    detail: DefinitionDetail, highlights: dict[int, list[Highlight[str]]], links: Links
) -> list[SourceLine]:
    return [
        source_line(
            line.text,
            [] if line.number is None else highlights.get(line.number, []),
            links,
            line.shown,
        )
        for line in detail.imports
    ]


def body_lines(
    detail: DefinitionDetail, highlights: dict[int, list[Highlight[str]]], links: Links
) -> list[SourceLine]:
    return [
        SourceLine(styled(line, highlights.get(detail.first_line + index, []), links))
        for index, line in enumerate(detail.text.split("\n"))
    ]


def head_lines(head: str, highlights: LineHighlights | None, links: Links) -> list[SourceLine]:
    lines = head.split("\n")
    found = highlights or []
    kept = lines if len(lines) <= HEAD_LINES else lines[: HEAD_LINES - 1]
    shown = [
        SourceLine(styled(line, found[index] if index < len(found) else [], links))
        for index, line in enumerate(kept)
    ]
    if len(kept) < len(lines):
        shown.append(SourceLine([Run([Piece(CUT, None)])]))
    return shown


@dataclass(frozen=True)
class Pages:
    first: int
    last: int
    total: int
    previous: str | None
    next: str | None


def with_offset(request: Request, parameter: str, offset: int) -> str:
    query = [
        (name, value) for name, value in request.query_params.multi_items() if name != parameter
    ]
    if offset:
        query.append((parameter, str(offset)))
    return f"{request.url.path}?{readable(query)}" if query else request.url.path


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
        filter_href=filter_href,
        reference=reference,
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
