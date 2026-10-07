import sqlite3
from collections.abc import Sequence
from pathlib import Path

from jend import hub
from jend.mirror import Mirror, Package
from jend.signatures import spans

DATABASE = "sources.sqlite"

SCHEMA = """
create table packages (
    hash text primary key,
    label text not null unique,
    description text not null,
    listed integer
) without rowid;
create table files (
    hash text not null,
    path text not null,
    text text not null,
    primary key (hash, path)
) without rowid;
"""


def label(package: Package) -> str:
    return package.hash if package.name is None else f"{package.name}@{package.version}"


def write(directory: Path, mirror: Mirror, listed: Sequence[Package], files: Path) -> None:
    position = {package.hash: index for index, package in enumerate(listed)}
    packages = [mirror.base, *mirror.packages]
    connection = sqlite3.connect(directory / DATABASE)
    with connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "insert into packages values (?, ?, ?, ?)",
            (
                (package.hash, label(package), package.description, position.get(package.hash))
                for package in packages
            ),
        )
        connection.executemany(
            "insert into files values (?, ?, ?)",
            (
                (
                    package.hash,
                    file.path,
                    hub.cached_path(files, package.hash, file.path).read_text(encoding="utf-8"),
                )
                for package in packages
                for file in package.files
            ),
        )
    connection.execute("vacuum")
    connection.close()


class Sources:
    def __init__(self, directory: Path) -> None:
        uri = f"{(directory / DATABASE).resolve().as_uri()}?immutable=1"
        self.connection = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def packages(self) -> list[tuple[str, str]]:
        return self.connection.execute(
            "select label, description from packages where listed is not null order by listed"
        ).fetchall()

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

    def definition(self, package: str, path: str, name: str) -> str | None:
        text = self.text(package, path)
        if text is None:
            return None
        span = spans(text).get(name)
        if span is None:
            return None
        start, end = span
        return "\n".join(text.splitlines()[start:end])

    def close(self) -> None:
        self.connection.close()
