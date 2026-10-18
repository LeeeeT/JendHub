import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote, urlencode

from jendhub.indexing.failures import Problem
from jendhub.indexing.status import GenerationStatus, InvalidRelease
from jendhub.search.packages import ReleaseDetail
from jendhub.search.sources import ReleaseFiles, id_source, usage
from jendhub.services.site.pages import shortened
from jendhub.services.site.views import DefinitionView, PackageView, Result, Results, StatusView

INDENT = "   "
NAMED_FILES = 5
SOURCE_TOOL = "source"


class Links(Protocol):
    def source(self, file: str) -> str: ...

    def definition(self, id_: str) -> str: ...

    def next(self, parameter: str, offset: int, shown: int, total: int) -> str: ...


def more(offset: int, shown: int, total: int) -> bool:
    return shown > 0 and offset + shown < total


def call(tool: str, arguments: dict[str, str | int]) -> str:
    given = ", ".join(f"{name}={json.dumps(value)}" for name, value in arguments.items())
    return f"{tool}({given})"


@dataclass(frozen=True)
class ToolLinks:
    tool: str
    arguments: dict[str, str | int] = field(default_factory=dict[str, str | int])

    def source(self, file: str) -> str:
        return call(SOURCE_TOOL, {"path": file})

    def definition(self, id_: str) -> str:
        file, name = id_source(id_)
        return call(SOURCE_TOOL, {"path": file, "definition": name})

    def next(self, parameter: str, offset: int, shown: int, total: int) -> str:
        if not more(offset, shown, total):
            return ""
        return f"More: {call(self.tool, self.arguments | {parameter: offset + shown})}\n"


@dataclass(frozen=True)
class UrlLinks:
    origin: str
    path: str = "/"
    query: dict[str, str] = field(default_factory=dict[str, str])

    def source(self, file: str) -> str:
        return f"{self.origin}/src/{quote(file, safe='/@')}"

    def definition(self, id_: str) -> str:
        file, name = id_source(id_)
        return f"{self.source(file)}?{urlencode({'def': name})}"

    def release(self, release: str) -> str:
        return f"{self.source(release)}/"

    def next(self, parameter: str, offset: int, shown: int, total: int) -> str:
        if not more(offset, shown, total):
            return ""
        query = urlencode(self.query | {parameter: str(offset + shown)})
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
    more = links.next("offset", found.offset, len(found.results), found.total)
    return "\n".join([*blocks, more] if more else blocks)


def render_definition(view: DefinitionView, links: Links) -> str:
    detail, used = view.detail, view.usage
    where = "Base" if detail.package is None else f"{detail.package}@{detail.version}"
    code = "\n".join([*detail.imports, "", detail.text]) if detail.imports else detail.text
    lines = f"lines {detail.first_line + 1}-{detail.end_line}"
    return (
        f"{used.call_name} ({detail.kind}, {where}, {lines})\n"
        f"Use: {used.import_line}, then call {used.call_name}.\n"
        f"File: {links.source(used.file)}\n\n"
        f"{code}\n\n"
        + heading("Uses", len(detail.uses), len(detail.uses), 0)
        + "".join(f"  {links.definition(id_)}\n" for id_ in detail.uses)
    )


def render_problem(problem: Problem, links: Links) -> str:
    places = ", ".join(f"{links.source(place.file)} line {place.line}" for place in problem.places)
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
    line = f"  {release.version}  {release.hash}  {release.state}  {links.release(label)}\n"
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
        + links.next(
            "definitions_offset", view.offset, len(package.definitions), package.definitions_total
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
        + links.next("invalid_releases_offset", view.offset, len(view.invalid), view.invalid_total)
    )


def render_files(files: ReleaseFiles, links: Links) -> str:
    return f"{files.release}\n\n" + "".join(
        f"  {links.source(f'{files.release}/{path}')}\n" for path in files.paths
    )
