import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from jend import base, hub
from jend.loader import BASE, Definition, FileKey, Import, import_lines
from jend.mirror import File, Mirror, Package
from jend.parser import module_of

DATABASE = "sources.sqlite"

SCHEMA = """
create table packages (
    hash text primary key,
    label text not null unique
) without rowid;
create table files (
    hash text not null,
    path text not null,
    text text not null,
    primary key (hash, path)
) without rowid;
create table blocks (
    hash text not null,
    path text not null,
    name text not null,
    block integer not null,
    source_hash text not null,
    source_path text not null,
    imports text not null,
    spans text not null,
    primary key (hash, path, name, block)
) without rowid;
"""


@dataclass(frozen=True)
class Block:
    package: str
    path: str
    text: str


def label(package: Package) -> str:
    return package.hash if package.name is None else f"{package.name}@{package.version}"


def absolute_target(module: str, labels: dict[str, str]) -> str:
    package, _, path = module.partition("/")
    return f"{labels[package]}/{path}.bend"


def absolute_imports(text: str, imports: tuple[Import, ...], labels: dict[str, str]) -> str:
    if not imports:
        return text
    lines = text.split("\n")
    aliased = [line for line in import_lines(text) if line.alias is not None]
    for line, imported in zip(aliased, imports, strict=True):
        ending = "\r" if lines[line.index].endswith("\r") else ""
        target = absolute_target(imported.module, labels)
        lines[line.index] = f"import {target} as {imported.alias}{ending}"
    return "\n".join(lines)


def used_imports(file: File, module: str, definition: Definition, labels: dict[str, str]) -> str:
    used = {module_of(ref) for ref in (*definition.refs, definition.key)} - {BASE, module}
    return "\n".join(
        f"import {absolute_target(imported.module, labels)} as {imported.alias}"
        for imported in file.imports
        if imported.module in used
    )


def write(directory: Path, mirror: Mirror, files: Path) -> None:
    packages = [mirror.base, *mirror.packages]
    labels = {package.hash: label(package) for package in packages}
    connection = sqlite3.connect(directory / DATABASE)
    with connection:
        connection.executescript(SCHEMA)
        connection.executemany("insert into packages values (?, ?)", labels.items())
        connection.executemany(
            "insert into files values (?, ?, ?)",
            (
                (
                    package.hash,
                    file.path,
                    absolute_imports(
                        hub.cached_path(files, package.hash, file.path).read_text(encoding="utf-8"),
                        file.imports,
                        labels,
                    ),
                )
                for package in packages
                for file in package.files
            ),
        )
        connection.executemany(
            "insert into blocks values (?, ?, ?, ?, ?, ?, ?, ?)", _blocks(mirror, labels)
        )
    connection.execute("vacuum")
    connection.close()


def _blocks(
    mirror: Mirror, labels: dict[str, str]
) -> Iterator[tuple[str, str, str, int, str, str, str, str]]:
    packages = [mirror.base, *mirror.packages]
    files = {(package.hash, file.path): file for package in packages for file in package.files}
    base_file = (mirror.base.hash, base.PATH)
    for package in packages:
        for file in package.files:
            for definition in file.definitions:
                for index, (module, spans) in enumerate(_spans(definition)):
                    source = _file(module, base_file)
                    yield (
                        package.hash,
                        file.path,
                        definition.name,
                        index,
                        *source,
                        used_imports(files[source], module, definition, labels),
                        json.dumps(spans),
                    )


def _spans(definition: Definition) -> list[tuple[str, list[tuple[int, int]]]]:
    home = module_of(definition.key)
    spans = {home: [(definition.first_line, definition.last_line)]}
    for fill in definition.fills:
        spans.setdefault(fill.module, []).append((fill.first_line, fill.last_line))
    others = sorted(module for module in spans if module != home)
    return [(module, spans[module]) for module in [home, *others]]


def _file(module: str, base_file: FileKey) -> FileKey:
    if module == BASE:
        return base_file
    package, _, path = module.partition("/")
    return package, f"{path}.bend"


class Sources:
    def __init__(self, directory: Path) -> None:
        uri = f"{(directory / DATABASE).resolve().as_uri()}?immutable=1"
        self.connection = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def _hash(self, package: str) -> str | None:
        found = self.connection.execute(
            "select hash from packages where label = ? or hash = ?", (package, package)
        ).fetchone()
        return None if found is None else found[0]

    def files(self, package: str) -> list[str] | None:
        package_hash = self._hash(package)
        if package_hash is None:
            return None
        rows = self.connection.execute(
            "select path from files where hash = ? order by path", (package_hash,)
        )
        return [path for (path,) in rows]

    def text(self, package: str, path: str) -> str | None:
        found = self.connection.execute(
            "select text from files join packages using (hash)"
            " where (label = ? or hash = ?) and path = ?",
            (package, package, path),
        ).fetchone()
        return None if found is None else found[0]

    def definition(self, package: str, path: str, name: str) -> list[Block] | None:
        package_hash = self._hash(package)
        if package_hash is None:
            return None
        rows = self.connection.execute(
            "select label, source_path, imports, spans, text from blocks"
            " join files on files.hash = source_hash and files.path = source_path"
            " join packages on packages.hash = source_hash"
            " where blocks.hash = ? and blocks.path = ? and name = ? order by block",
            (package_hash, path, name),
        ).fetchall()
        if not rows:
            return None
        return [
            Block(source_label, source_path, _block_text(imports, json.loads(spans), text))
            for source_label, source_path, imports, spans, text in rows
        ]

    def close(self) -> None:
        self.connection.close()


def _block_text(imports: str, spans: list[list[int]], text: str) -> str:
    lines = text.split("\n")
    parts = [
        "\n".join(line.removesuffix("\r") for line in lines[first - 1 : last])
        for first, last in spans
    ]
    return "\n\n".join([imports, *parts] if imports else parts)
