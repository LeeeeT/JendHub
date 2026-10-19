import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

from jendhub.indexing.failures import Problem
from jendhub.indexing.status import GenerationStatus, InvalidRelease
from jendhub.search.packages import ReleaseDetail
from jendhub.search.sources import id_source, usage
from jendhub.services.site.arguments import DEFINITION, FILES, MAX_COLUMNS, READ
from jendhub.services.site.files import Lines, Page
from jendhub.services.site.pages import shortened
from jendhub.services.site.views import DefinitionView, PackageView, Result, Results, StatusView

INDENT = "   "
NAMED_FILES = 5

type Argument = str | int | bool | list[str]


class Links(Protocol):
    def definition(self, id_: str) -> str: ...

    def read(
        self, file: str, start_line: int | None = None, end_line: int | None = None
    ) -> str: ...

    def next(self, parameter: str, value: int) -> str: ...


def more(offset: int, shown: int, total: int) -> bool:
    return shown > 0 and offset + shown < total


def following(links: Links, parameter: str, offset: int, shown: int, total: int) -> str:
    return links.next(parameter, offset + shown) if more(offset, shown, total) else ""


def given(**arguments: Argument | None) -> dict[str, Argument]:
    return {name: value for name, value in arguments.items() if value is not None}


def definition_arguments(id_: str) -> dict[str, Argument]:
    file, name = id_source(id_)
    return {"path": file, "name": name}


def call(tool: str, arguments: Mapping[str, Argument]) -> str:
    named = ", ".join(f"{name}={json.dumps(value)}" for name, value in arguments.items())
    return f"{tool}({named})"


def encoded(query: Mapping[str, Argument]) -> str:
    return urlencode(query, doseq=True, safe="/@*:")


@dataclass(frozen=True)
class ToolLinks:
    tool: str
    arguments: Mapping[str, Argument] = field(default_factory=dict[str, Argument])

    def definition(self, id_: str) -> str:
        return call(DEFINITION.name, definition_arguments(id_))

    def read(self, file: str, start_line: int | None = None, end_line: int | None = None) -> str:
        return call(READ.name, given(path=file, start_line=start_line, end_line=end_line))

    def next(self, parameter: str, value: int) -> str:
        return f"More: {call(self.tool, {**self.arguments, parameter: value})}\n"


@dataclass(frozen=True)
class UrlLinks:
    origin: str
    path: str = "/"
    query: Sequence[tuple[str, str]] = ()

    def definition(self, id_: str) -> str:
        return f"{self.origin}/{DEFINITION.name}.txt?{encoded(definition_arguments(id_))}"

    def read(self, file: str, start_line: int | None = None, end_line: int | None = None) -> str:
        query = given(path=file, start_line=start_line, end_line=end_line)
        return f"{self.origin}/{READ.name}.txt?{encoded(query)}"

    def files(self, path: str) -> str:
        return f"{self.origin}/{FILES.name}.txt?{encoded({'path': path})}"

    def next(self, parameter: str, value: int) -> str:
        kept = [(name, given) for name, given in self.query if name != parameter]
        query = urlencode([*kept, (parameter, str(value))], safe="/@*:")
        return f"More: {self.origin}{self.path}?{query}\n"


def indented(text: str) -> str:
    return "".join(f"{INDENT}{line}\n" for line in text.splitlines())


def heading(title: str, shown: int, total: int, offset: int) -> str:
    part = f", {offset + 1} to {offset + shown} shown" if shown < total else ""
    return f"{title} ({total}{part}):\n"


def render_result(rank: int, result: Result, links: Links) -> str:
    hit = result.hit
    return f"{rank}. {result.usage.call_name} ({links.definition(hit.id)})\n" + indented(
        f"{result.usage.import_line}\n{shortened(hit.head)}"
    )


def render_results(found: Results, links: Links) -> str:
    if not found.results:
        return "No results.\n"
    blocks = [
        render_result(rank, result, links)
        for rank, result in enumerate(found.results, start=found.offset + 1)
    ]
    next_page = following(links, "offset", found.offset, len(found.results), found.total)
    return "\n".join([*blocks, next_page] if next_page else blocks)


def render_definition(view: DefinitionView, links: Links) -> str:
    detail, used = view.detail, view.usage
    where = "Base" if detail.package is None else f"{detail.package}@{detail.version}"
    first, last = detail.first_line + 1, detail.end_line
    code = "\n".join([*detail.imports, "", detail.text]) if detail.imports else detail.text
    return (
        f"{used.call_name}: {detail.kind} of {where}\n"
        f"Import: {used.import_line}\n"
        f"Lines {first}-{last} of {used.file}: {links.read(used.file, first, last)}\n\n"
        f"{code}\n\n"
        + heading("Uses", len(detail.uses), len(detail.uses), 0)
        + "".join(f"  {links.definition(id_)}\n" for id_ in detail.uses)
    )


def render_lines(found: Lines, links: Links) -> str:
    if not found.total:
        return f"{found.path} is empty.\n"
    numbered = "".join(
        f"{number}\t{line[:MAX_COLUMNS]}\n"
        for number, line in enumerate(found.lines, start=found.first)
    )
    if not found.cut:
        return numbered
    cut = f"Lines {found.first}-{found.last} of {found.total}. "
    return numbered + cut + links.next("start_line", found.last + 1)


def render_page(found: Page, links: Links, empty: str) -> str:
    if not found.entries:
        return f"{empty}\n"
    entries = "".join(f"{entry}\n" for entry in found.entries)
    end = found.offset + len(found.entries)
    return entries + (links.next("offset", end) if found.more else "")


def render_matches(found: Page, links: Links) -> str:
    return render_page(found, links, "No matches.")


def render_paths(found: Page, links: Links) -> str:
    return render_page(found, links, "No files match.")


def render_problem(problem: Problem, links: Links) -> str:
    places = ", ".join(links.read(place.file, place.line) for place in problem.places)
    where = f"At {places}.\n" if places else ""
    named = ", ".join(problem.files[:NAMED_FILES])
    files = (
        named
        if len(problem.files) <= NAMED_FILES
        else f"{named} and {len(problem.files) - NAMED_FILES} more"
    )
    return f"{problem.message}\n{where}Files that do not load: {files}.\n"


def render_problems(problems: list[Problem], links: Links) -> str:
    return "".join(indented(render_problem(problem, links)) for problem in problems)


def render_release(name: str, release: ReleaseDetail, links: UrlLinks) -> str:
    label = f"{name}@{release.version}"
    line = f"  {release.version}  {release.hash}  {release.state}  {links.files(label)}\n"
    return line + render_problems(release.problems, links)


def render_package(view: PackageView, links: UrlLinks) -> str:
    package = view.package
    definitions = "".join(
        f"  {call_name(package.name, definition.id, definition.name, definition.module)}"
        f" ({links.definition(definition.id)})\n" + indented(shortened(definition.head))
        for definition in package.definitions
    )
    return (
        f"{package.name}\n\n"
        + heading("Releases", len(package.releases), len(package.releases), 0)
        + "".join(render_release(package.name, release, links) for release in package.releases)
        + "\n"
        + heading("Definitions", len(package.definitions), package.definitions_total, view.offset)
        + definitions
        + following(
            links,
            "definitions_offset",
            view.offset,
            len(package.definitions),
            package.definitions_total,
        )
    )


def call_name(package: str, id_: str, name: str, module: str) -> str:
    version = id_.partition("@")[2].partition("/")[0]
    return usage(name, package, version, module).call_name


def render_generation(generation: GenerationStatus) -> str:
    jobs = generation.jobs
    return (
        f"Generation {generation.id} ({generation.state}): Base {generation.base_tag},"
        f" vectors {generation.vectors}, indexer {generation.indexer_version},"
        f" {generation.releases} releases, {generation.definitions} definitions\n"
        f"  Jobs: {jobs.queued} queued, {jobs.running} running, {jobs.done} indexed,"
        f" {jobs.superseded} superseded, {jobs.failed} failed\n"
    )


def render_invalid(release: InvalidRelease, links: Links) -> str:
    return (
        f"  {release.label} ({release.hash}), Base {release.base_tag},"
        f" {release.found_at:%Y-%m-%d}\n" + render_problems(release.problems, links)
    )


def render_status(view: StatusView, links: Links) -> str:
    generations = "".join(render_generation(generation) for generation in view.generations)
    return (
        (generations or "No generation.\n")
        + "\n"
        + heading("Invalid releases", len(view.invalid), view.invalid_total, view.offset)
        + "".join(render_invalid(release, links) for release in view.invalid)
        + following(
            links, "invalid_releases_offset", view.offset, len(view.invalid), view.invalid_total
        )
    )
