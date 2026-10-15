import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from jendhub.definitions import BASE, Corpus, Definition, Origin
from jendhub.errors import JendHubError
from jendhub.hub import Json
from jendhub.mirror import Mirror

INDEXER = "jendhub-index"


class UsesError(JendHubError):
    pass


@dataclass(frozen=True)
class UseGraph:
    users: dict[str, frozenset[str]]
    used: dict[str, frozenset[str]]
    errors: dict[str, str]


def corpus_files(corpus: Corpus, full: Mirror) -> dict[Path, Origin]:
    hashes = full.releases()
    files = {full.base_source.resolve(): BASE}
    for release in sorted(corpus.releases, key=str):
        root = full.lib / hashes[str(release)]
        for path in sorted(full.read_package(hashes[str(release)])):
            files[(root / path).resolve()] = Origin(release, path.removesuffix(".bend"))
    return files


def run_indexer(full: Mirror, entries: list[Path]) -> list[Json]:
    request = {
        "lib": str(full.lib),
        "base": str(full.base_source.resolve()),
        "entries": [str(entry) for entry in entries],
    }
    try:
        result = subprocess.run(  # noqa: S603
            [INDEXER], input=json.dumps(request), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise UsesError(f"{INDEXER} is not on PATH: run jendhub in nix develop") from error
    if result.returncode != 0:
        raise UsesError(f"{INDEXER} failed: {result.stderr.strip()}")
    answer: Json = json.loads(result.stdout)
    match answer:
        case {"entries": list() as found} if len(found) == len(entries):
            return found
        case _:
            raise UsesError(f"{INDEXER} returned an invalid answer")


def declaration_id(files: dict[Path, Origin], declaration: Json) -> str | None:
    match declaration:
        case {"file": str() as file, "name": str() as name}:
            origin = files.get(Path(file))
            return None if origin is None else f"{origin.prefix}{name}"
        case _:
            raise UsesError(f"{INDEXER} returned an invalid declaration: {declaration!r}")


def use_graph(corpus: Corpus, full: Mirror, definitions: list[Definition]) -> UseGraph:
    files = corpus_files(corpus, full)
    ids = {definition.id for definition in definitions}
    users: defaultdict[str, set[str]] = defaultdict(set)
    used: defaultdict[str, set[str]] = defaultdict(set)
    errors: dict[str, str] = {}
    for entry in run_indexer(full, list(files)):
        match entry:
            case {"path": str() as path, "error": error, "uses": list() as uses}:
                if isinstance(error, str):
                    errors[files[Path(path)].label] = error
            case _:
                raise UsesError(f"{INDEXER} returned an invalid entry: {entry!r}")
        for use in uses:
            match use:
                case {"from": source, "to": target}:
                    user = declaration_id(files, source)
                    target_id = declaration_id(files, target)
                case _:
                    raise UsesError(f"{INDEXER} returned an invalid use: {use!r}")
            if user in ids and target_id in ids:
                users[target_id].add(user)
                used[user].add(target_id)
    return UseGraph(
        {id_: frozenset(found) for id_, found in users.items()},
        {id_: frozenset(found) for id_, found in used.items()},
        errors,
    )
