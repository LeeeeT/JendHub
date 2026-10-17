from jendhub.corpus.definitions import Definition, Origin

DOCUMENT_LIMIT = 32_000


def origin_line(origin: Origin) -> str:
    return "Base" if origin.release is None else f"{origin.release.name} {origin.module}"


def origin_document(definition: Definition) -> str:
    return f"{origin_line(definition.origin)}\n{definition.text}"[:DOCUMENT_LIMIT]


def name_document(definition: Definition) -> str:
    return f"{origin_line(definition.origin)}\n{definition.name}"
