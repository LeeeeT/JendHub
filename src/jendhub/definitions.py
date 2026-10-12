import re
from dataclasses import dataclass
from typing import Literal, cast

from jendhub.hub import Release
from jendhub.mirror import Mirror

DECLARATION = re.compile(r"(?:@unsafe )?(def|type|law) ([A-Za-z_][\w.]*)")
IMPORT_ALIAS = re.compile(r"^import \S+ as (\w+)", re.MULTILINE)
TRAILING_COMMENT = re.compile(r"\s+#.*$")

type Kind = Literal["def", "type", "law"]


@dataclass(frozen=True)
class Origin:
    release: Release | None
    module: str

    @property
    def prefix(self) -> str:
        return "" if self.release is None else f"{self.release}/{self.module}:"


BASE = Origin(None, "")


@dataclass(frozen=True)
class SourceFile:
    origin: Origin
    source: str


@dataclass(frozen=True)
class Definition:
    id: str
    kind: Kind
    name: str
    origin: Origin
    text: str
    head: str


@dataclass(frozen=True)
class Block:
    kind: Kind
    name: str
    doc: list[str]
    lines: list[str]

    @property
    def head(self) -> list[str]:
        if self.kind != "def":
            return self.doc + self.lines
        return self.doc + self.lines[: signature_length(self.lines)]


def ends_block(line: str) -> bool:
    return line.startswith(("#", "import ")) or DECLARATION.match(line) is not None


def signature_length(lines: list[str]) -> int:
    depth = 0
    for index, line in enumerate(lines):
        depth += line.count("(") - line.count(")")
        if depth <= 0 and TRAILING_COMMENT.sub("", line).rstrip().endswith(":"):
            return index + 1
    return len(lines)


def doc_comment(lines: list[str], start: int) -> list[str]:
    end = start
    while end > 0 and lines[end - 1].startswith("#"):
        end -= 1
    return lines[end:start]


def blocks(source: str) -> list[Block]:
    lines = source.splitlines()
    found: list[Block] = []
    for start, line in enumerate(lines):
        match = DECLARATION.match(line)
        if match is None:
            continue
        end = start + 1
        while end < len(lines) and not ends_block(lines[end]):
            end += 1
        body = "\n".join(lines[start:end]).rstrip().split("\n")
        kind = cast("Kind", match.group(1))
        found.append(Block(kind, match.group(2), doc_comment(lines, start), body))
    return found


def extract(file: SourceFile) -> list[Definition]:
    aliases = tuple(f"{alias}." for alias in IMPORT_ALIAS.findall(file.source))
    found = blocks(file.source)
    laws = {block.name for block in found if block.kind == "law"}
    return [
        Definition(
            f"{file.origin.prefix}{block.name}",
            block.kind,
            block.name,
            file.origin,
            "\n".join(block.doc + block.lines),
            "\n".join(block.head),
        )
        for block in found
        if not block.name.startswith(aliases) and not (block.kind == "def" and block.name in laws)
    ]


def source_files(corpus: Mirror) -> list[SourceFile]:
    files = [SourceFile(BASE, corpus.base_source.read_text(encoding="utf-8"))]
    for release, hash_ in corpus.releases().items():
        for path, content in sorted(corpus.read_package(hash_).items()):
            origin = Origin(Release.parse(release), path.removesuffix(".bend"))
            files.append(SourceFile(origin, content.decode()))
    return files


def corpus_definitions(corpus: Mirror) -> list[Definition]:
    return [definition for file in source_files(corpus) for definition in extract(file)]
