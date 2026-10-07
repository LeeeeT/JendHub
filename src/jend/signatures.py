import re
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    DEF = "def"
    TYPE = "type"
    LAW = "law"


@dataclass(frozen=True)
class Definition:
    kind: Kind
    name: str
    signature: str
    doc: str
    line: int


@dataclass(frozen=True)
class Extraction:
    definitions: tuple[Definition, ...]
    unparsed_lines: tuple[int, ...]


_DECLARATION = re.compile(r"(?:@unsafe\s+)?(def|type|law)\s")
_TOP_LEVEL = re.compile(r"(?:@unsafe\b|def\s|type\s|law\s|import\s)")
_NAME = {
    Kind.DEF: re.compile(r"(?:@unsafe\s+)?def\s+([^\s(<?]+)"),
    Kind.TYPE: re.compile(r"type\s+([^\s<:]+)"),
    Kind.LAW: re.compile(r"law\s+([^\s:]+)"),
}
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = set(_OPENERS.values())
_BINDER = re.compile(r"[@&]-?[\w.]+\s*$")


def extract(source: str) -> Extraction:
    lines = source.splitlines()
    starts = [index for index, line in enumerate(lines) if _TOP_LEVEL.match(line)]
    definitions: list[Definition] = []
    unparsed: list[int] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        declaration = _DECLARATION.match(lines[start])
        name = _NAME[Kind(declaration[1])].match(lines[start]) if declaration else None
        if declaration is None or name is None:
            continue
        kind = Kind(declaration[1])
        block = lines[start:end]
        if kind is Kind.DEF:
            signature = _def_header(block)
            if signature is None:
                unparsed.append(start + 1)
                continue
            if not re.search(r"\)\s*->", signature):
                continue
        else:
            signature = _block(block)
        anchor = start - 1 if start > 0 and lines[start - 1].strip() == "@unsafe" else start
        definitions.append(Definition(kind, name[1], signature, _doc(lines, anchor), start + 1))
    return Extraction(tuple(definitions), tuple(unparsed))


def spans(source: str) -> dict[str, tuple[int, int]]:
    lines = source.splitlines()
    starts = [index for index, line in enumerate(lines) if _TOP_LEVEL.match(line)]
    result: dict[str, tuple[int, int]] = {}
    for position, start in enumerate(starts):
        declaration = _DECLARATION.match(lines[start])
        name = _NAME[Kind(declaration[1])].match(lines[start]) if declaration else None
        if name is None:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        first = start - 1 if start > 0 and lines[start - 1].strip() == "@unsafe" else start
        while first > 0 and lines[first - 1].startswith("#"):
            first -= 1
        result.setdefault(name[1], (first, end))
    return result


def _block(block: list[str]) -> str:
    kept = list(block)
    while kept and (not kept[-1].strip() or kept[-1].startswith("#")):
        kept.pop()
    return "\n".join(line.rstrip() for line in kept if line.strip())


def _def_header(block: list[str]) -> str | None:
    text = "\n".join(block)
    header: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in "'\"":
            end = _literal_end(text, index)
            header.append(text[index:end])
            index = end
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth -= 1
        elif char == ":" and depth == 0 and not _BINDER.search(text, max(0, index - 64), index):
            return _collapse("".join(header))
        header.append(char)
        index += 1
    return None


def _literal_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text) and text[index] != quote:
        index += 2 if text[index] == "\\" else 1
    return index + 1


def _collapse(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([(\[{])\s", r"\1", text)
    return re.sub(r"\s([)\]}])", r"\1", text).strip()


def _doc(lines: list[str], anchor: int) -> str:
    doc: list[str] = []
    index = anchor - 1
    while index >= 0 and lines[index].startswith("#"):
        doc.append(lines[index].lstrip("#").strip())
        index -= 1
    return " ".join(reversed(doc))
