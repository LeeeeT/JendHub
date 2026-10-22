import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from jendhub.corpus.definitions import BASE, Definition, Origin, SourceFile, extract
from jendhub.hub.api import Release
from jendhub.hub.library import Library
from jendhub.indexing.failures import LoadError, Place
from jendhub.search.documents import name_document, origin_document
from jendhub.search.highlights import LineHighlights
from jendhub.search.signatures import Signature, head_signature
from jendhub.search.sources import BASE_FILE, file_path
from jendhub.uses.graph import UseGraph, reached
from jendhub.uses.resolve import Declaration, ResolvedFile, resolve

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
    highlights: dict[str, LineHighlights]


def definition_key(origin: Origin, name: str) -> str:
    return name if origin.release is None else f"{origin.release.name}/{origin.module}:{name}"


def source_files(
    library: Library, base: Path, release: str, package: Release | None
) -> dict[Path, SourceFile]:
    if package is None:
        return {base.resolve(): SourceFile(BASE, base.read_text(encoding="utf-8"))}
    root = library.package_directory(release)
    return {
        (root / path).resolve(): SourceFile(
            Origin(package, path.removesuffix(".bend")), content.decode(errors="replace")
        )
        for path, content in sorted(library.read_package(release).items())
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

    def key(self, target: Declaration) -> str | None:
        if target.file == self.base:
            return target.name
        hash_, *path = target.file.relative_to(self.library).parts
        package = self.owners.get(hash_)
        if package is None:
            return None
        module = "/".join(path).removesuffix(".bend")
        return f"{package.name}/{module}:{target.name}"


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
    resolved: list[ResolvedFile],
    files: dict[Path, SourceFile],
    library: Path,
    base: Path,
    owners: Owners,
) -> dict[str, LoadError]:
    failed = [(file.path, file.error) for file in resolved if file.error is not None]
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


async def build(
    library: Library, base: Path, release: str, package: Release | None, owners: Owners
) -> Artifacts:
    files = source_files(library, base, release, package)
    paths = {file.origin: path for path, file in files.items()}
    definitions = [found for file in files.values() for found in extract(file)]
    by_id = {definition.id: definition for definition in definitions}
    resolved = await asyncio.to_thread(resolve, library.lib, base, list(files))
    lib = library.lib.resolve()
    targets = {
        hash_
        for file in resolved
        for use in file.uses
        if (hash_ := package_hash(lib, use.target.file)) is not None
    }
    real_base = await asyncio.to_thread(base.resolve)
    found_targets = Targets(lib, real_base, await owners(sorted(targets)))
    edges: list[tuple[str, str]] = []
    external: defaultdict[str, set[str]] = defaultdict(set)
    for file in resolved:
        for use in file.uses:
            user = f"{files[use.user.file].origin.prefix}{use.user.name}"
            if user not in by_id:
                continue
            if use.target.file in files:
                target = f"{files[use.target.file].origin.prefix}{use.target.name}"
                if target in by_id:
                    edges.append((user, target))
            elif (key := found_targets.key(use.target)) is not None and (
                package is None or not key.startswith(f"{package.name}/")
            ):
                external[user].add(key)
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
    errors = await load_errors(resolved, files, lib, real_base, owners)
    extracted: defaultdict[Path, set[str]] = defaultdict(set)
    for definition in definitions:
        extracted[paths[definition.origin]].add(definition.name)
    mismatches = {
        files[file.path].origin.label: Mismatch(
            frozenset(extracted[file.path] - file.declarations),
            frozenset(file.declarations - extracted[file.path]),
        )
        for file in resolved
        if file.error is None and frozenset(extracted[file.path]) != file.declarations
    }
    highlights = {files[file.path].origin.module: file.highlights for file in resolved}
    return Artifacts(release, package, indexed, sorted(set(edges)), errors, mismatches, highlights)
