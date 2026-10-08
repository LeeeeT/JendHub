import sqlite3
from collections.abc import Iterator
from pathlib import Path

from jend import hub
from jend.loader import Import, import_lines
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
create table definitions (
    hash text not null,
    path text not null,
    name text not null,
    first_line integer not null,
    last_line integer not null,
    imports text not null,
    primary key (hash, path, name)
) without rowid;
"""


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


def used_imports(file: File, module: str, refs: tuple[str, ...], labels: dict[str, str]) -> str:
    used = {module_of(ref) for ref in refs} - {"", module}
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
            "insert into definitions values (?, ?, ?, ?, ?, ?)", _definitions(packages, labels)
        )
    connection.execute("vacuum")
    connection.close()


def _definitions(
    packages: list[Package], labels: dict[str, str]
) -> Iterator[tuple[str, str, str, int, int, str]]:
    for package in packages:
        for file in package.files:
            for definition in file.definitions:
                yield (
                    package.hash,
                    file.path,
                    definition.name,
                    definition.first_line,
                    definition.last_line,
                    used_imports(file, module_of(definition.key), definition.refs, labels),
                )


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
        package_hash = self._hash(package)
        if package_hash is None:
            return None
        found = self.connection.execute(
            "select first_line, last_line, imports, text from definitions join files"
            " using (hash, path) where hash = ? and path = ? and name = ?",
            (package_hash, path, name),
        ).fetchone()
        if found is None:
            return None
        first, last, imports, text = found
        lines = text.split("\n")[first : last + 1]
        code = "\n".join(line.removesuffix("\r") for line in lines)
        return f"{imports}\n\n{code}" if imports else code

    def close(self) -> None:
        self.connection.close()
