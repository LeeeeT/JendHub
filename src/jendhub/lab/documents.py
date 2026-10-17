from collections.abc import Callable

from jendhub.corpus.definitions import Definition, Origin
from jendhub.enrich.enricher import Enrichment
from jendhub.search.documents import origin_line


def origin_words(origin: Origin) -> str:
    return "" if origin.release is None else f"{origin.release.name} {origin.module}"


def text_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(definition.id, definition.text) for definition in definitions]


def head_package_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [
        (definition.id, f"{origin_words(definition.origin)}\n{definition.head}")
        for definition in definitions
    ]


def name_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(definition.id, definition.name) for definition in definitions]


def origin_name_document(definition: Definition) -> str:
    return f"{origin_line(definition.origin)}\n{definition.name}"


def metadata(enrichment: Enrichment) -> str:
    return "\n".join([enrichment.summary, *enrichment.queries])


def enriched_view(
    definitions: list[Definition],
    enrichments: dict[str, Enrichment],
    view: Callable[[Definition, Enrichment], str],
) -> list[tuple[str, str]]:
    return [
        (d.id, d.text if (e := enrichments.get(d.id)) is None else view(d, e)) for d in definitions
    ]


def text_summary_documents(
    definitions: list[Definition], enrichments: dict[str, Enrichment]
) -> list[tuple[str, str]]:
    return enriched_view(definitions, enrichments, lambda d, e: f"{d.text}\n\n{e.summary}")


def enriched_documents(
    definitions: list[Definition], enrichments: dict[str, Enrichment]
) -> list[tuple[str, str]]:
    return enriched_view(definitions, enrichments, lambda d, e: f"{d.text}\n\n{metadata(e)}")


def metadata_documents(
    definitions: list[Definition], enrichments: dict[str, Enrichment]
) -> list[tuple[str, str]]:
    return enriched_view(definitions, enrichments, lambda _d, e: metadata(e))
