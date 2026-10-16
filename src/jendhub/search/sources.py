import posixpath
import re
from dataclasses import dataclass

from psycopg import AsyncConnection

from jendhub.corpus.definitions import IMPORT
from jendhub.db.pool import Pool
from jendhub.hub.api import HASH, Release, valid_release

BASE = "Base"
BASE_FILE = "base.bend"
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


async def base_source(connection: AsyncConnection, generation: int) -> str | None:
    cursor = await connection.execute(
        "SELECT b.source FROM generations g JOIN base_releases b ON b.tag = g.base_tag"
        " WHERE g.id = %s",
        (generation,),
    )
    row = await cursor.fetchone()
    return None if row is None else bytes(row[0]).decode(errors="replace")


async def package_source(
    connection: AsyncConnection, release: StoredRelease, path: str
) -> str | None:
    cursor = await connection.execute(
        "SELECT content FROM package_files WHERE hash = %s AND path = %s", (release.hash, path)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    text = bytes(row[0]).decode(errors="replace")
    labels = await hash_labels(connection, imported_hashes(text))
    return absolute_imports(text, release.label, path, labels)


async def read_source(
    connection: AsyncConnection, generation: int, release: str, path: str
) -> SourceFile | None:
    if release == BASE:
        text = await base_source(connection, generation) if path == BASE_FILE else None
        return None if text is None else SourceFile(BASE, None, BASE_FILE, text)
    stored = await stored_release(connection, release)
    if stored is None:
        return None
    text = await package_source(connection, stored, path)
    return None if text is None else SourceFile(stored.label, stored.hash, path, text)


async def read_file(pool: Pool, generation: int, release: str, path: str) -> SourceFile | None:
    async with pool.connection() as connection:
        return await read_source(connection, generation, release, path)


async def read_release_files(pool: Pool, release: str) -> ReleaseFiles | None:
    if release == BASE:
        return ReleaseFiles(BASE, None, [BASE_FILE])
    async with pool.connection() as connection:
        stored = await stored_release(connection, release)
        if stored is None:
            return None
        cursor = await connection.execute(
            "SELECT path FROM package_files WHERE hash = %s ORDER BY path", (stored.hash,)
        )
        paths = [path for (path,) in await cursor.fetchall()]
    return ReleaseFiles(stored.label, stored.hash, paths)
