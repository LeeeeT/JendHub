from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from jendhub.services.contracts import (
    DefinitionPage,
    FileList,
    GenerationSummary,
    InvalidReleaseSummary,
    PackagePage,
    ReleaseSummary,
    SearchHit,
    SearchResults,
    StatusPage,
)

INDENT = "   "


@dataclass(frozen=True)
class Links:
    origin: str
    path: str = "/"
    query: dict[str, str] = field(default_factory=dict[str, str])

    def definition(self, id_: str) -> str:
        return f"{self.origin}/definitions/{quote(id_, safe='/@:')}"

    def file(self, path: str) -> str:
        return f"{self.origin}/files/{quote(path, safe='/@')}"

    def every_definition(self) -> str:
        return f"Each ID is at {self.origin}/definitions/<id>\n"

    def next(self, parameter: str, offset: int, shown: int, total: int) -> str:
        if shown == 0 or offset + shown >= total:
            return ""
        query = urlencode(self.query | {parameter: str(offset + shown)})
        return f"More: {self.origin}{self.path}?{query}\n"


def indented(text: str) -> str:
    return "".join(f"{INDENT}{line}\n" for line in text.splitlines())


def render_hit(rank: int, hit: SearchHit, links: Links) -> str:
    return f"{rank}. {hit.call_name} ({links.definition(hit.id)})\n" + indented(
        f"{hit.import_line}\n{hit.head}"
    )


def render_results(found: SearchResults, links: Links) -> str:
    if not found.results:
        return "No results.\n"
    blocks = [
        render_hit(rank, hit, links)
        for rank, hit in enumerate(found.results, start=found.offset + 1)
    ]
    more = links.next("offset", found.offset, len(found.results), found.total)
    return "\n".join([*blocks, more] if more else blocks)


def heading(title: str, shown: int, total: int, offset: int) -> str:
    part = f", {offset + 1} to {offset + shown} shown" if shown < total else ""
    return f"{title} ({total}{part}):\n"


def id_list(title: str, ids: list[str], total: int, offset: int) -> str:
    return heading(title, len(ids), total, offset) + "".join(f"  {id_}\n" for id_ in ids)


def render_release(name: str, release: ReleaseSummary, links: Links) -> str:
    files = links.file(f"{name}@{release.version}")
    line = f"  {release.version}  {release.hash}  {release.state}  {files}\n"
    return line if release.reason is None else line + indented(release.reason)


def render_package(page: PackagePage, links: Links) -> str:
    definitions = "".join(
        f"  {definition.id}\n" + indented(definition.head) for definition in page.definitions
    )
    return (
        f"{page.name}\n\n"
        + heading("Releases", len(page.releases), len(page.releases), 0)
        + "".join(render_release(page.name, release, links) for release in page.releases)
        + "\n"
        + heading(
            "Definitions", len(page.definitions), page.definitions_total, page.definitions_offset
        )
        + definitions
        + links.next(
            "definitions_offset",
            page.definitions_offset,
            len(page.definitions),
            page.definitions_total,
        )
        + "\n"
        + links.every_definition()
    )


def render_generation(generation: GenerationSummary) -> str:
    jobs = generation.jobs
    return (
        f"Generation {generation.id} ({generation.state}): Base {generation.base_tag},"
        f" vectors {generation.vectors}, indexer {generation.indexer_version},"
        f" {generation.releases} releases,"
        f" {generation.definitions} definitions\n"
        f"  Jobs: {jobs.queued} queued, {jobs.running} running, {jobs.done} done,"
        f" {jobs.superseded} superseded, {jobs.invalid} invalid, {jobs.failed} failed\n"
    )


def render_invalid(release: InvalidReleaseSummary) -> str:
    name = release.hash if release.name is None else f"{release.name}@{release.version}"
    return (
        f"  {name} ({release.hash}), Base {release.base_tag}, {release.found_at:%Y-%m-%d}\n"
        + indented(release.reason)
    )


def render_status(page: StatusPage, links: Links) -> str:
    generations = "".join(render_generation(generation) for generation in page.generations)
    return (
        (generations or "No generation.\n")
        + "\n"
        + heading(
            "Invalid releases",
            len(page.invalid_releases),
            page.invalid_releases_total,
            page.invalid_releases_offset,
        )
        + "".join(render_invalid(release) for release in page.invalid_releases)
        + links.next(
            "invalid_releases_offset",
            page.invalid_releases_offset,
            len(page.invalid_releases),
            page.invalid_releases_total,
        )
    )


def render_definition(page: DefinitionPage, links: Links) -> str:
    where = "Base" if page.package is None else f"{page.package}@{page.version}"
    code = "\n".join([*page.imports, "", page.text]) if page.imports else page.text
    return (
        f"{label_of(page)} ({page.kind}, {where}, lines {page.first_line + 1}-{page.end_line})\n"
        f"Use: {page.import_line}, then call {page.call_name}.\n"
        f"File: {links.file(page.file)}\n\n"
        f"{code}\n\n"
        + id_list("Uses", page.uses, len(page.uses), 0)
        + "\n"
        + links.every_definition()
    )


def label_of(page: DefinitionPage) -> str:
    return page.call_name if page.package is None else f"{page.call_name} ({page.id})"


def render_files(page: FileList, links: Links) -> str:
    return f"{page.release}\n\n" + "".join(
        f"  {links.file(f'{page.release}/{path}')}\n" for path in page.paths
    )
