from fastapi import Request

QUERY = "query"
FILTERS = frozenset({"package", "uses", "used_by"})


def box_text(request: Request) -> str:
    words = [
        value.strip() if name == QUERY else f"{name}={value}"
        for name, value in request.query_params.multi_items()
        if name == QUERY or name in FILTERS
    ]
    return " ".join(word for word in words if word)


def box_filter(word: str) -> tuple[str, str] | None:
    name, equals, value = word.partition("=")
    return (name, value) if equals and value and name in FILTERS else None


def box_parameters(text: str) -> list[tuple[str, str]]:
    parameters: list[tuple[str, str]] = []
    words: list[str] = []
    place = None
    for word in text.split():
        if (found := box_filter(word)) is not None:
            parameters.append(found)
            continue
        if place is None:
            place = len(parameters)
        words.append(word)
    if place is not None:
        parameters.insert(place, (QUERY, " ".join(words)))
    return parameters


def typed_filters(request: Request) -> bool:
    return any(
        box_filter(word) is not None
        for value in request.query_params.getlist(QUERY)
        for word in value.split()
    )
