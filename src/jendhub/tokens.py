import re

WORD = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
SEPARATOR = re.compile(r"[._]")
CAMEL_PART = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z]?[a-z]+[0-9]*|[0-9]+")


def word_tokens(word: str) -> list[str]:
    whole = word.lower()
    found = [whole]
    for segment in SEPARATOR.split(word):
        parts = CAMEL_PART.findall(segment)
        if segment and segment.lower() != whole:
            found.append(segment.lower())
        if len(parts) > 1:
            found += [part.lower() for part in parts]
    return found


def tokens(text: str) -> list[str]:
    return [token for word in WORD.findall(text) for token in word_tokens(word)]
