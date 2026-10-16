from jendhub.search.tokens import tokens


def name_tokens(text: str) -> set[str]:
    return set(tokens(text))


def overlap(query: set[str], name: set[str]) -> float:
    union = query | name
    return len(query & name) / len(union) if union else 0.0
