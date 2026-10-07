import posixpath
import re
import sqlite3
from pathlib import Path

from jend import hub
from jend.mirror import Mirror, Package
from jend.signatures import spans

DATABASE = "sources.sqlite"
IMPORT = re.compile(r"^import (\S+) as (\w+)[ \t]*$", re.MULTILINE)

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
"""


def label(package: Package) -> str:
    return package.hash if package.name is None else f"{package.name}@{package.version}"


def absolute_imports(package: str, path: str, text: str) -> str:
    folder = posixpath.dirname(path)

    def absolute(match: re.Match[str]) -> str:
        target, alias = match[1], match[2]
        if not target.startswith(("./", "../")):
            return match[0]
        resolved = posixpath.normpath(posixpath.join(folder, target))
        if resolved.startswith("../"):
            return match[0]
        return f"import {package}/{resolved} as {alias}"

    return IMPORT.sub(absolute, text)


def write(directory: Path, mirror: Mirror, files: Path) -> None:
    packages = [mirror.base, *mirror.packages]
    connection = sqlite3.connect(directory / DATABASE)
    with connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "insert into packages values (?, ?)",
            ((package.hash, label(package)) for package in packages),
        )
        connection.executemany(
            "insert into files values (?, ?, ?)",
            (
                (
                    package.hash,
                    file.path,
                    absolute_imports(
                        label(package),
                        file.path,
                        hub.cached_path(files, package.hash, file.path).read_text(encoding="utf-8"),
                    ),
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
        code = "\n".join(text.splitlines()[start:end])
        used = [
            f"import {target} as {alias}"
            for target, alias in IMPORT.findall(text)
            if re.search(rf"\b{re.escape(alias)}\.", code)
        ]
        return "\n".join([*used, "", code]) if used else code

    def close(self) -> None:
        self.connection.close()
