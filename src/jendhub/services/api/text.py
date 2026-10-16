from jendhub.services.contracts import (
    DefinitionPage,
    GenerationSummary,
    InvalidReleaseSummary,
    PackagePage,
    ReleaseSummary,
    SearchHit,
    SearchResults,
    StatusPage,
)

INDENT = "   "


def label(hit: SearchHit) -> str:
    return f"{hit.id} (Base)" if hit.package is None else hit.id


def indented(text: str) -> str:
    return "".join(f"{INDENT}{line}\n" for line in text.splitlines())


def render_results(found: SearchResults) -> str:
    if not found.results:
        return "No results.\n"
    blocks = [
        f"{rank}. {label(hit)}\n" + indented(hit.head)
        for rank, hit in enumerate(found.results, start=1)
    ]
    return "\n".join(blocks)


def heading(title: str, shown: int, total: int, offset: int) -> str:
    part = f", {offset + 1} to {offset + shown} shown" if shown < total else ""
    return f"{title} ({total}{part}):\n"


def id_list(title: str, ids: list[str], total: int, offset: int) -> str:
    return heading(title, len(ids), total, offset) + "".join(f"  {id_}\n" for id_ in ids)


def render_release(release: ReleaseSummary) -> str:
    line = f"  {release.version}  {release.hash}  {release.state}\n"
    return line if release.reason is None else line + indented(release.reason)


def render_package(page: PackagePage) -> str:
    definitions = "".join(
        f"  {definition.id}\n" + indented(definition.head) for definition in page.definitions
    )
    return (
        f"{page.name}\n\n"
        + heading("Releases", len(page.releases), len(page.releases), 0)
        + "".join(render_release(release) for release in page.releases)
        + "\n"
        + heading(
            "Definitions", len(page.definitions), page.definitions_total, page.definitions_offset
        )
        + definitions
    )


def render_generation(generation: GenerationSummary) -> str:
    jobs = generation.jobs
    return (
        f"Generation {generation.id} ({generation.state}): Base {generation.base_tag},"
        f" indexer {generation.indexer_version}, {generation.releases} releases,"
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


def render_status(page: StatusPage) -> str:
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
    )


def render_definition(page: DefinitionPage) -> str:
    where = "Base" if page.package is None else f"{page.package}@{page.version}, {page.module}.bend"
    return (
        f"{page.id} ({page.kind}, {where}, lines {page.first_line + 1}-{page.end_line})\n\n"
        f"{page.text}\n\n"
        + id_list("Uses", page.uses, len(page.uses), 0)
        + "\n"
        + id_list("Used by", page.used_by, page.used_by_total, page.used_by_offset)
    )
