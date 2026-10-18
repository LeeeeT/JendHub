import asyncio
import itertools
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg import AsyncConnection

from jendhub.corpus.definitions import IMPORT
from jendhub.db.pool import Pool
from jendhub.hub.api import HASH, Release, valid_path, valid_release
from jendhub.hub.library import Library, file_paths

BASE = "Base"
BASE_FILE = "base.bend"
BASE_RELEASE = "base"
BASE_IMPORT = "import Base"

HASH_TARGET = re.compile(rf"^({HASH.pattern})/(.+)$")
WORD = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class SourceFile:
    release: str
    hash: str | None
    path: str
    text: str


@dataclass(frozen=True)
class ReleaseFiles:
    release: str
    hash: str | None
    paths: list[str]
    definitions: dict[str, int]


@dataclass(frozen=True)
class Usage:
    import_line: str
    call_name: str
    file: str


@dataclass(frozen=True)
class StoredRelease:
    label: str
    hash: str | None


def alias(package: str, module: str) -> str:
    stem = posixpath.basename(module)
    words = [word for word in WORD.split(package if stem == "main" else stem) if word]
    name = "".join(word[0].upper() + word[1:] for word in words) or "P"
    return name if name[0].isalpha() else f"M{name}"


def import_line(package: str | None, version: str | None, module: str) -> str:
    if package is None:
        return BASE_IMPORT
    return f"import {package}@{version}/{module}.bend as {alias(package, module)}"


def call_name(name: str, package: str | None, module: str) -> str:
    return name if package is None else f"{alias(package, module)}.{name}"


def file_path(package: str | None, version: str | None, module: str) -> str:
    return f"{BASE}/{BASE_FILE}" if package is None else f"{package}@{version}/{module}.bend"


def usage(name: str, package: str | None, version: str | None, module: str) -> Usage:
    return Usage(
        import_line(package, version, module),
        call_name(name, package, module),
        file_path(package, version, module),
    )


def id_source(id_: str) -> tuple[str, str]:
    release, slash, rest = id_.partition("/")
    if not slash or "@" not in release:
        return f"{BASE}/{BASE_FILE}", id_
    module, _, name = rest.partition(":")
    return f"{release}/{module}.bend", name


def label(hash_: str, name: str | None, version: str | None) -> str:
    return hash_ if name is None else f"{name}@{version}"


def absolute_line(line: str, release: str, directory: str, labels: dict[str, str]) -> str:
    match = IMPORT.match(line)
    if match is None:
        return line
    target, name, rest = match.groups()
    if target.startswith(("./", "../")):
        resolved = posixpath.normpath(posixpath.join(directory, target))
        if resolved == ".." or resolved.startswith("../"):
            return line
        return f"import {release}/{resolved} as {name}{rest}"
    hashed = HASH_TARGET.match(target)
    if hashed is not None and hashed.group(1) in labels:
        return f"import {labels[hashed.group(1)]}/{hashed.group(2)} as {name}{rest}"
    return line


def absolute_imports(text: str, release: str, path: str, labels: dict[str, str]) -> str:
    directory = posixpath.dirname(path)
    return "\n".join(absolute_line(line, release, directory, labels) for line in text.split("\n"))


def imported_hashes(text: str) -> list[str]:
    return [
        hashed.group(1)
        for line in text.split("\n")
        if (match := IMPORT.match(line)) is not None
        and (hashed := HASH_TARGET.match(match.group(1))) is not None
    ]


def used_imports(file_text: str, code: str) -> list[str]:
    used: list[str] = []
    for line in file_text.split("\n"):
        if line.rstrip() == BASE_IMPORT:
            used.append(BASE_IMPORT)
            continue
        match = IMPORT.match(line)
        if match is not None and re.search(rf"(?<![\w.]){match.group(2)}\.", code):
            used.append(line.rstrip())
    return list(dict.fromkeys(used))


async def stored_release(connection: AsyncConnection, release: str) -> StoredRelease | None:
    if HASH.fullmatch(release):
        cursor = await connection.execute(
            "SELECT name, version FROM packages WHERE hash = %s", (release,)
        )
        row = await cursor.fetchone()
        return None if row is None else StoredRelease(label(release, *row), release)
    name, _, version = release.partition("@")
    if not valid_release(name, version):
        return None
    cursor = await connection.execute(
        "SELECT hash FROM packages WHERE name = %s AND version = %s", (name, version)
    )
    row = await cursor.fetchone()
    return None if row is None else StoredRelease(str(Release(name, version)), row[0])


async def hash_labels(connection: AsyncConnection, hashes: list[str]) -> dict[str, str]:
    cursor = await connection.execute(
        "SELECT hash, name, version FROM packages WHERE hash = ANY(%s) AND name IS NOT NULL",
        (hashes,),
    )
    return {hash_: label(hash_, name, version) for hash_, name, version in await cursor.fetchall()}


async def base_tag(connection: AsyncConnection, generation: int) -> str | None:
    cursor = await connection.execute(
        "SELECT base_tag FROM generations WHERE id = %s", (generation,)
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]


def read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode(errors="replace")
    except FileNotFoundError, IsADirectoryError, NotADirectoryError:
        return None


async def file_text(directory: Path, path: str) -> str | None:
    if not valid_path(path):
        return None
    return await asyncio.to_thread(read_text, directory / path)


async def package_source(
    connection: AsyncConnection, library: Library, release: StoredRelease, path: str
) -> str | None:
    if release.hash is None:
        return None
    text = await file_text(library.package_directory(release.hash), path)
    if text is None:
        return None
    labels = await hash_labels(connection, imported_hashes(text))
    return absolute_imports(text, release.label, path, labels)


async def read_file(
    pool: Pool, library: Library, generation: int, release: str, path: str
) -> SourceFile | None:
    async with pool.connection() as connection:
        if release == BASE:
            tag = await base_tag(connection, generation) if path == BASE_FILE else None
            text = None if tag is None else await file_text(library.base_directory(tag), path)
            return None if text is None else SourceFile(BASE, None, BASE_FILE, text)
        stored = await stored_release(connection, release)
        if stored is None:
            return None
        text = await package_source(connection, library, stored, path)
    return None if text is None else SourceFile(stored.label, stored.hash, path, text)


@dataclass(frozen=True)
class Declared:
    id: str
    name: str
    line: int


def declared_line(first_line: int, head: str) -> int:
    comments = len(list(itertools.takewhile(lambda line: line.startswith("#"), head.split("\n"))))
    return first_line + comments + 1


async def read_declared(pool: Pool, generation: int, file: SourceFile) -> list[Declared]:
    release = BASE_RELEASE if file.hash is None else file.hash
    module = "" if file.hash is None else file.path.removesuffix(".bend")
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id, name, first_line, head FROM definitions"
            " WHERE generation = %s AND release = %s AND module = %s ORDER BY first_line",
            (generation, release, module),
        )
        rows = await cursor.fetchall()
    return [Declared(id_, name, declared_line(first, head)) for id_, name, first, head in rows]


async def read_release_files(
    pool: Pool, library: Library, generation: int, release: str
) -> ReleaseFiles | None:
    async with pool.connection() as connection:
        if release == BASE:
            cursor = await connection.execute(
                "SELECT count(*) FROM definitions WHERE generation = %s AND package IS NULL",
                (generation,),
            )
            row = await cursor.fetchone()
            return ReleaseFiles(BASE, None, [BASE_FILE], {BASE_FILE: 0 if row is None else row[0]})
        stored = await stored_release(connection, release)
        if stored is None or stored.hash is None:
            return None
        directory = library.package_directory(stored.hash)
        paths = await asyncio.to_thread(file_paths, directory) if directory.is_dir() else []
        cursor = await connection.execute(
            "SELECT module || '.bend', count(*) FROM definitions"
            " WHERE generation = %s AND release = %s GROUP BY module",
            (generation, stored.hash),
        )
        definitions: dict[str, int] = dict(await cursor.fetchall())
    return ReleaseFiles(stored.label, stored.hash, paths, definitions)
