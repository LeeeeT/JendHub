import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from jend.loader import import_lines
from jend.mirror import (
    BaseFile,
    File,
    FileRef,
    Import,
    Law,
    Mirror,
    Origin,
    PackageFile,
    Tld,
    file_ref,
    files,
    label,
    origin_hash,
)

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
    text text not null,
    primary key (hash, path, name, block)
) without rowid;
"""


@dataclass(frozen=True)
class Block:
    package: str
    path: str
    text: str


def absolute_target(file: PackageFile, labels: dict[str, str]) -> str:
    return f"{labels[file.package]}/{file.path}"


def absolute_imports(text: str, imports: tuple[Import, ...], labels: dict[str, str]) -> str:
    lines = text.split("\n")
    aliased = [line for line in import_lines(text) if line.alias is not None]
    for line, imported in zip(aliased, imports, strict=True):
        lines[line.index] = f"import {absolute_target(imported.file, labels)} as {imported.alias}"
    return "\n".join(lines)


def write(directory: Path, mirror: Mirror) -> None:
    origins: list[Origin] = [mirror.base, *mirror.packages]
    labels = {origin_hash(origin): label(origin) for origin in origins}
    by_ref = {file_ref(origin, file): file for origin in origins for file in files(origin)}
    connection = sqlite3.connect(directory / DATABASE)
    with connection:
        connection.executescript(SCHEMA)
        connection.executemany("insert into packages values (?, ?)", labels.items())
        connection.executemany(
            "insert into files values (?, ?, ?)",
            (
                (origin_hash(origin), file.path, absolute_imports(file.text, file.imports, labels))
                for origin in origins
                for file in files(origin)
            ),
        )
        connection.executemany(
            "insert into blocks values (?, ?, ?, ?, ?, ?, ?)", _blocks(by_ref, labels)
        )
    connection.execute("vacuum")
    connection.close()


def _blocks(
    files: dict[FileRef, File], labels: dict[str, str]
) -> Iterator[tuple[str, str, str, int, str, str, str]]:
    for home, file in files.items():
        for tld in file.tlds:
            for index, (source, text) in enumerate(_view(tld, home, files, labels)):
                yield (_ref_hash(home), file.path, tld.name, index, _ref_hash(source),
                       files[source].path, text)  # fmt: skip


def _view(
    tld: Tld, home: FileRef, files: dict[FileRef, File], labels: dict[str, str]
) -> list[tuple[FileRef, str]]:
    used = {home, *(key.file for key in tld.refs)}
    head = ["import Base"] if any(isinstance(file, BaseFile) for file in used - {home}) else []
    fill = tld.kind.fill if isinstance(tld.kind, Law) else None
    pieces = [_piece(tld.doc, tld.code)]
    if fill is not None and fill.file == home:
        pieces.append(_piece(fill.doc, fill.code))
    blocks = [(home, _block([*head, *_import_lines(files[home], used, labels)], pieces))]
    if fill is not None and fill.file != home:
        lines = _import_lines(files[fill.file], used, labels)
        blocks.append((fill.file, _block(lines, [_piece(fill.doc, fill.code)])))
    return blocks


def _import_lines(file: File, used: set[FileRef], labels: dict[str, str]) -> list[str]:
    return [
        f"import {absolute_target(imported.file, labels)} as {imported.alias}"
        for imported in file.imports
        if imported.file in used
    ]


def _block(head: list[str], pieces: list[str]) -> str:
    return "\n\n".join(["\n".join(head), *pieces] if head else pieces)


def _piece(doc: str, code: str) -> str:
    return f"{doc}\n{code}" if doc else code


def _ref_hash(file: FileRef) -> str:
    return file.commit if isinstance(file, BaseFile) else file.package


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
            "select label, source_path, text from blocks"
            " join packages on packages.hash = source_hash"
            " where blocks.hash = ? and blocks.path = ? and name = ? order by block",
            (package_hash, path, name),
        ).fetchall()
        if not rows:
            return None
        return [Block(source_label, source_path, text) for source_label, source_path, text in rows]

    def close(self) -> None:
        self.connection.close()
