import re
from collections import Counter
from dataclasses import dataclass

from jendhub.json import Json

DECLARATION = re.compile(r"(?:@unsafe )?def [A-Za-z_][\w.]*\s*\(")
IDENTIFIER = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
MULTIPLICITY = re.compile(r"&\d+\s*,\s*")
TRAILING_COMMENT = re.compile(r"#.*$")
OPENING = "<([{"
CLOSING = ">)]}"
ARROW = "->"
SORTS = frozenset({"Type", "Data"})
TYPE_VARIABLE = "T"

type Types = Counter[str]


@dataclass(frozen=True)
class Signature:
    parameters: list[Types]
    result: Types

    def parameters_json(self) -> Json:
        return [dict[str, Json](parameter) for parameter in self.parameters]

    def result_json(self) -> Json:
        return dict[str, Json](self.result)

    @staticmethod
    def from_json(parameters: list[dict[str, int]], result: dict[str, int]) -> Signature:
        return Signature([Counter(parameter) for parameter in parameters], Counter(result))


def depth_change(text: str, index: int) -> int:
    character = text[index]
    if character in OPENING:
        return 1
    if character in CLOSING and text[max(index - 1, 0) : index + 1] != ARROW:
        return -1
    return 0


def split_outside_brackets(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        if depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index].strip())
            index += len(separator)
            start = index
            continue
        depth += depth_change(text, index)
        index += 1
    parts.append(text[start:].strip())
    return parts


def type_name(identifier: str) -> str:
    name = identifier.rsplit(".", 1)[-1]
    return TYPE_VARIABLE if len(name) == 1 and name.isupper() else name


def types(text: str) -> Types:
    names = (type_name(found) for found in IDENTIFIER.findall(MULTIPLICITY.sub("", text)))
    return Counter(name for name in names if name not in SORTS)


def parameter_types(parameters: str) -> list[Types]:
    found: list[Types] = []
    for parameter in split_outside_brackets(parameters, ","):
        _, colon, annotation = parameter.partition(":")
        annotation = annotation.strip()
        if colon and annotation and annotation not in SORTS:
            found.append(types(annotation))
    return found


def head_signature(head: str) -> Signature | None:
    line = " ".join(text.strip() for text in head.splitlines() if not text.startswith("#"))
    declaration = DECLARATION.match(line)
    if declaration is None:
        return None
    depth = 1
    index = declaration.end()
    while index < len(line) and depth:
        depth += depth_change(line, index)
        index += 1
    rest = TRAILING_COMMENT.sub("", line[index:]).strip().removesuffix(":").strip()
    result = rest.removeprefix(ARROW) if rest.startswith(ARROW) else ""
    return Signature(parameter_types(line[declaration.end() : index - 1]), types(result))


def query_signature(query: str) -> Signature | None:
    if query.startswith("def "):
        return head_signature(query)
    parts = split_outside_brackets(query, ARROW)
    if len(parts) == 1:
        return None
    parameters = split_outside_brackets(ARROW.join(parts[:-1]), ",")
    return Signature([types(text) for text in parameters if text], types(parts[-1]))


def overlap(wanted: Types, found: Types) -> float:
    union = (wanted | found).total()
    return (wanted & found).total() / union if union else 1.0


def similarity(wanted: Signature, found: Signature) -> float:
    remaining = list(found.parameters)
    matched = 0.0
    for parameter in wanted.parameters:
        if not remaining:
            break
        best = max(remaining, key=lambda candidate: overlap(parameter, candidate))
        matched += overlap(parameter, best)
        remaining.remove(best)
    count = max(len(wanted.parameters), len(found.parameters))
    parameters = matched / count if count else 1.0
    return (overlap(wanted.result, found.result) + parameters) / 2
