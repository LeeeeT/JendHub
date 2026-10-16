from jendhub.services.contracts import DefinitionPage, SearchHit, SearchResults

INDENT = "   "


def label(hit: SearchHit) -> str:
    return f"{hit.id} (Base)" if hit.package is None else hit.id


def render_results(found: SearchResults) -> str:
    if not found.results:
        return "No results.\n"
    blocks = [
        f"{rank}. {label(hit)}\n" + "".join(f"{INDENT}{line}\n" for line in hit.head.splitlines())
        for rank, hit in enumerate(found.results, start=1)
    ]
    return "\n".join(blocks)


def id_list(title: str, ids: list[str], total: int, offset: int) -> str:
    shown = f", {offset + 1} to {offset + len(ids)} shown" if len(ids) < total else ""
    return f"{title} ({total}{shown}):\n" + "".join(f"  {id_}\n" for id_ in ids)


def render_definition(page: DefinitionPage) -> str:
    where = "Base" if page.package is None else f"{page.package}@{page.version}, {page.module}.bend"
    return (
        f"{page.id} ({page.kind}, {where}, lines {page.first_line + 1}-{page.end_line})\n\n"
        f"{page.text}\n\n"
        + id_list("Uses", page.uses, len(page.uses), 0)
        + "\n"
        + id_list("Used by", page.used_by, page.used_by_total, page.used_by_offset)
    )
