import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from jendhub.corpus.definitions import BASE, IMPORT, Definition, Origin, SourceFile, extract
from jendhub.hub.api import Release
from jendhub.hub.library import Library
from jendhub.search.documents import name_document, origin_document
from jendhub.search.signatures import Signature, head_signature
from jendhub.search.sources import BASE_FILE, file_path
from jendhub.uses.failures import LoadError, Place
from jendhub.uses.graph import UseGraph, reached
from jendhub.uses.highlights import Highlight
from jendhub.uses.resolve import Declaration, Failure, ResolvedFile, resolve

type Owners = Callable[[list[str]], Awaitable[dict[str, Release | None]]]


@dataclass(frozen=True)
class IndexedDefinition:
    definition: Definition
    key: str
    document: str
    name_document: str
    parent: str | None
    signature: Signature | None
    documented: bool
    helper: bool
    release_users: int
    external_targets: frozenset[str]
    used_keys: frozenset[str]


@dataclass(frozen=True)
class Mismatch:
    only_extracted: frozenset[str]
    only_declared: frozenset[str]


@dataclass(frozen=True)
class Artifacts:
    release: str
    package: Release | None
    definitions: list[IndexedDefinition]
    uses: list[tuple[str, str]]
    errors: dict[str, LoadError]
    mismatches: dict[str, Mismatch]
    highlights: dict[str, str]


def definition_key(origin: Origin, name: str) -> str:
    return name if origin.release is None else f"{origin.release.name}/{origin.module}:{name}"


def source_files(library: Library, base: Path, package: Release | None) -> dict[Path, SourceFile]:
    if package is None:
        return {base.resolve(): SourceFile(BASE, base.read_text(encoding="utf-8"))}
    hash_ = library.release_hash(package)
    root = library.package_directory(hash_)
    return {
        (root / path).resolve(): SourceFile(
            Origin(package, path.removesuffix(".bend")), content.decode(errors="replace")
        )
        for path, content in sorted(library.read_package(hash_).items())
    }


def is_helper(definition: Definition, graph: UseGraph, by_id: dict[str, Definition]) -> bool:
    return any(
        by_id[user].origin == definition.origin
        and definition.name.startswith(f"{by_id[user].name}.")
        for user in reached(definition.id, graph.users)
    )


@dataclass(frozen=True)
class Targets:
    library: Path
    base: Path
    owners: dict[str, Release | None]

    def origin(self, target: Declaration) -> Origin | None:
        if target.file == self.base:
            return BASE
        if not target.file.is_relative_to(self.library):
            return None
        hash_, *path = target.file.relative_to(self.library).parts
        package = self.owners.get(hash_)
        if package is None:
            return None
        return Origin(package, "/".join(path).removesuffix(".bend"))

    def key(self, target: Declaration) -> str | None:
        origin = self.origin(target)
        return None if origin is None else definition_key(origin, target.name)


def linked(
    lines: list[list[Highlight[Declaration]]], keys: Callable[[Declaration], str | None]
) -> str:
    found = [
        [
            [
                item.start,
                item.end,
                item.kind,
                item.declaration,
                None if item.target is None else keys(item.target),
            ]
            for item in line
        ]
        for line in lines
    ]
    return json.dumps(found, separators=(",", ":"))


def parents(definitions: list[Definition]) -> dict[str, str]:
    named = {(definition.origin, definition.name): definition.id for definition in definitions}
    return {
        definition.id: parent
        for definition in definitions
        if "." in definition.name
        and (parent := named.get((definition.origin, definition.name.rsplit(".", 1)[0])))
        is not None
    }


def package_hash(library: Path, file: Path) -> str | None:
    return file.relative_to(library).parts[0] if file.is_relative_to(library) else None


def file_name(origin: Origin) -> str:
    return BASE_FILE if origin.release is None else f"{origin.module}.bend"


async def load_errors(
    failed: list[tuple[Path, Failure]],
    files: dict[Path, SourceFile],
    library: Path,
    base: Path,
    owners: Owners,
) -> dict[str, LoadError]:
    if not failed:
        return {}
    hashes = {
        hash_
        for _, error in failed
        if error.file is not None and (hash_ := package_hash(library, error.file)) is not None
    }
    found = await owners(sorted(hashes))

    def place(file: Path | None, line: int | None) -> Place | None:
        if file is None or line is None:
            return None
        if file == base:
            return Place(file_path(None, None, ""), line)
        if (hash_ := package_hash(library, file)) is None:
            return None
        owner = found.get(hash_)
        path = file.relative_to(library / hash_).as_posix()
        return Place(f"{hash_ if owner is None else owner}/{path}", line)

    return {
        file_name(files[path].origin): LoadError(
            error.expected, error.observed, place(error.file, error.line)
        )
        for path, error in failed
    }


def imported_owners(
    library: Library,
    files: dict[Path, SourceFile],
    package: Release | None,
    selected: dict[str, Release | None],
) -> dict[str, Release | None]:
    for source in files.values():
        for match in IMPORT.finditer(source.source):
            label, _, _ = match.group(1).partition("/")
            name, at, version = label.partition("@")
            if at:
                imported = Release(name, version)
                with suppress(FileNotFoundError):
                    selected[library.release_hash(imported)] = imported
    if package is not None:
        selected[library.release_hash(package)] = package
    return selected


@dataclass
class Analysis:
    definitions: list[Definition]
    edges: set[tuple[str, str]]
    external: defaultdict[str, set[str]]
    errors: dict[str, LoadError]
    mismatches: dict[str, Mismatch]
    highlights: dict[str, str]

    def add_uses(
        self,
        file: ResolvedFile,
        files: dict[Path, SourceFile],
        by_id: dict[str, Definition],
        targets: Targets,
        package: Release | None,
    ) -> None:
        for use in file.uses:
            user = f"{files[use.user.file].origin.prefix}{use.user.name}"
            if user not in by_id:
                continue
            if use.target.file in files:
                target = f"{files[use.target.file].origin.prefix}{use.target.name}"
                if target in by_id:
                    self.edges.add((user, target))
            elif (key := targets.key(use.target)) is not None and (
                package is None or not key.startswith(f"{package.name}/")
            ):
                self.external[user].add(key)


async def analyze(
    library: Library,
    base: Path,
    package: Release | None,
    files: dict[Path, SourceFile],
    owners: Owners,
) -> Analysis:
    definitions = [found for file in files.values() for found in extract(file)]
    analysis = Analysis(definitions, set(), defaultdict(set), {}, {}, {})
    by_id = {definition.id: definition for definition in definitions}
    extracted: defaultdict[Origin, set[str]] = defaultdict(set)
    for definition in definitions:
        extracted[definition.origin].add(definition.name)
    lib = library.lib.resolve()
    real_base = await asyncio.to_thread(base.resolve)
    failed: list[tuple[Path, Failure]] = []
    async with resolve(lib, base, list(files)) as resolved:
        for file in resolved:
            hashes = {
                hash_
                for target in (
                    *(use.target for use in file.uses),
                    *(found.target for line in file.highlights for found in line),
                )
                if target is not None and (hash_ := package_hash(lib, target.file)) is not None
            }
            selected = await owners(sorted(hashes))
            targets = Targets(
                lib,
                real_base,
                imported_owners(library, {file.path: files[file.path]}, package, selected),
            )
            analysis.add_uses(file, files, by_id, targets, package)
            if file.error is not None:
                failed.append((file.path, file.error))
            origin = files[file.path].origin
            if file.error is None and frozenset(extracted[origin]) != file.declarations:
                analysis.mismatches[origin.label] = Mismatch(
                    frozenset(extracted[origin] - file.declarations),
                    frozenset(file.declarations - extracted[origin]),
                )

            analysis.highlights[origin.module] = linked(
                file.highlights, target_keys(files, targets)
            )
    analysis.errors = await load_errors(failed, files, lib, real_base, owners)
    return analysis


def target_keys(
    files: dict[Path, SourceFile], targets: Targets
) -> Callable[[Declaration], str | None]:
    @cache
    def key(target: Declaration) -> str | None:
        if target.file in files:
            return definition_key(files[target.file].origin, target.name)
        return targets.key(target)

    return key


async def build(
    library: Library, base: Path, release: str, package: Release | None, owners: Owners
) -> Artifacts:
    files = source_files(library, base, package)
    analysis = await analyze(library, base, package, files, owners)
    definitions = analysis.definitions
    by_id = {definition.id: definition for definition in definitions}
    edges = analysis.edges
    external = analysis.external
    graph = UseGraph.of(edges)
    found_parents = parents(definitions)
    indexed = [
        IndexedDefinition(
            definition,
            definition_key(definition.origin, definition.name),
            origin_document(definition),
            name_document(definition),
            found_parents.get(definition.id),
            head_signature(definition.head) if definition.kind == "def" else None,
            definition.text.startswith("#"),
            is_helper(definition, graph, by_id),
            len(graph.users.get(definition.id, frozenset())),
            frozenset(external[definition.id]),
            frozenset(
                definition_key(by_id[target].origin, by_id[target].name)
                for target in graph.used.get(definition.id, frozenset())
            )
            | external[definition.id],
        )
        for definition in definitions
    ]
    return Artifacts(
        release,
        package,
        indexed,
        sorted(edges),
        analysis.errors,
        analysis.mismatches,
        analysis.highlights,
    )
