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
from jendhub.search.highlights import Highlight, LineHighlights
from jendhub.search.sources import BASE, BASE_FILE, Declared, SourceFile, id_source

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


def style(found: Highlight) -> str | None:
    if found.declaration and found.kind in DECLARED:
        return f"{found.kind} declared"
    return found.kind if found.kind in STYLED else None


def qualified(highlights: list[Highlight]) -> list[Highlight]:
    return [
        replace(found, kind=after.kind)
        if found.kind == "namespace"
        and not found.declaration
        and after is not None
        and after.start == found.end + 1
        else found
        for found, after in pairwise([*highlights, None])
        if found is not None
    ]


def styled(text: str, highlights: list[Highlight], start: int = 0) -> list[Piece]:
    pieces: list[Piece] = []
    done = 0
    for found in qualified(highlights):
        name = style(found)
        first = max(found.start - start, done)
        last = min(found.end - start, len(text))
        if name is None or first >= last:
            continue
        gap = text[done:first]
        if gap == "." and pieces and pieces[-1].style == name:
            pieces[-1] = Piece(pieces[-1].text + text[done:last], name)
        else:
            if gap:
                pieces.append(Piece(gap, None))
            pieces.append(Piece(text[first:last], name))
        done = last
    if done < len(text):
        pieces.append(Piece(text[done:], None))
    return pieces


def declaration_link(text: str, declared: Declared) -> Link | None:
    found = re.search(rf"(?<![\w.]){re.escape(declared.name)}(?![\w.])", text)
    if found is None:
        return None
    return Link(
        text[: found.start()], declared.name, definition_href(declared.id), text[found.end() :]
    )


def import_link(text: str) -> Link | None:
    if (base := BASE_IMPORT.match(text)) is not None:
        return Link(base.group(1), BASE, file_href(f"{BASE}/{BASE_FILE}"), base.group(3))
    if (found := IMPORT.match(text)) is not None:
        return Link(found.group(1), found.group(2), file_href(found.group(2)), found.group(3))
    return None


def linked(link: Link, before: list[Piece], label: list[Piece], after: list[Piece]) -> SourceLine:
    return SourceLine([Run(before), Run(label, link.href), Run(after)])


def styled_as(text: str, highlights: list[Highlight], start: int) -> list[Piece]:
    found = next((found for found in highlights if found.start == start), None)
    return [Piece(text, None if found is None else style(found))] if text else []


def source_line(
    text: str,
    highlights: list[Highlight],
    shown: str | None = None,
    declared: Declared | None = None,
) -> SourceLine:
    shown = text if shown is None else shown
    if (link := import_link(shown)) is not None:
        after = len(text) - len(link.after)
        if not (text.startswith(link.before) and text.endswith(link.after)):
            highlights = []
        return linked(
            link,
            styled(link.before, highlights),
            styled_as(link.label, highlights, len(link.before)),
            styled(link.after, highlights, after),
        )
    found = highlights if shown == text else []
    link = None if declared is None else declaration_link(shown, declared)
    if link is None:
        return SourceLine([Run(styled(shown, found))])
    after = len(shown) - len(link.after)
    return linked(
        link,
        styled(link.before, found),
        styled(link.label, found, len(link.before)),
        styled(link.after, found, after),
    )


def file_lines(
    file: SourceFile, declared: list[Declared], highlights: LineHighlights | None
) -> list[SourceLine]:
    by_line = {found.line: found for found in declared}
    lines = file.text.removesuffix("\n").split("\n") if file.text else []
    found = highlights if highlights is not None and len(highlights) == len(lines) else None
    return [
        source_line(
            line, [] if found is None else found[index], file.shown(line), by_line.get(index + 1)
        )
        for index, line in enumerate(lines)
    ]


def import_lines(
    detail: DefinitionDetail, highlights: dict[int, list[Highlight]]
) -> list[SourceLine]:
    return [
        source_line(
            line.text,
            [] if line.number is None else highlights.get(line.number, []),
            line.shown,
        )
        for line in detail.imports
    ]


def body_lines(
    detail: DefinitionDetail, highlights: dict[int, list[Highlight]]
) -> list[SourceLine]:
    return [
        SourceLine([Run(styled(line, highlights.get(detail.first_line + index, [])))])
        for index, line in enumerate(detail.text.split("\n"))
    ]


def head_lines(head: str, highlights: LineHighlights | None) -> list[SourceLine]:
    lines = head.split("\n")
    found = highlights or []
    kept = lines if len(lines) <= HEAD_LINES else lines[: HEAD_LINES - 1]
    shown = [
        SourceLine([Run(styled(line, found[index] if index < len(found) else []))])
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
