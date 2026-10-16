import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from jendhub.corpus.definitions import BASE, Definition, Origin, SourceFile, extract
from jendhub.hub.api import Release
from jendhub.hub.library import Library
from jendhub.search.documents import origin_document
from jendhub.search.signatures import Signature, head_signature
from jendhub.uses.graph import UseGraph, reached
from jendhub.uses.resolve import Declaration, resolve

BASE_RELEASE = "base"

type Owners = Callable[[list[str]], Awaitable[dict[str, Release | None]]]


@dataclass(frozen=True)
class IndexedDefinition:
    definition: Definition
    key: str
    document: str
    signature: Signature | None
    documented: bool
    helper: bool
    users: int
    reached: frozenset[str]
    external_targets: frozenset[str]


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
    errors: dict[str, str]
    mismatches: dict[str, Mismatch]


def definition_key(origin: Origin, name: str) -> str:
    return name if origin.release is None else f"{origin.release.name}/{origin.module}:{name}"


def source_files(
    library: Library, base: Path, release: str, package: Release | None
) -> dict[Path, SourceFile]:
    if package is None:
        return {base.resolve(): SourceFile(BASE, base.read_text(encoding="utf-8"))}
    root = library.lib / release
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


def package_hash(library: Path, file: Path) -> str | None:
    return file.relative_to(library).parts[0] if file.is_relative_to(library) else None


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
    found_targets = Targets(
        lib, await asyncio.to_thread(base.resolve), await owners(sorted(targets))
    )
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
    indexed = [
        IndexedDefinition(
            definition,
            definition_key(definition.origin, definition.name),
            origin_document(definition),
            head_signature(definition.head) if definition.kind == "def" else None,
            definition.text.startswith("#"),
            is_helper(definition, graph, by_id),
            len(graph.users.get(definition.id, frozenset())),
            frozenset(reached(definition.id, graph.used)),
            frozenset(external[definition.id]),
        )
        for definition in definitions
    ]
    errors = {files[file.path].origin.label: file.error for file in resolved if file.error}
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
    return Artifacts(release, package, indexed, sorted(set(edges)), errors, mismatches)
