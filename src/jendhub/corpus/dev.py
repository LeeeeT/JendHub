import json
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

import httpx

from jendhub.corpus.corpus import Corpus
from jendhub.corpus.definitions import BASE, SourceFile, extract
from jendhub.errors import JendHubError
from jendhub.hub.api import Listing, PackageRelease, Release, listings, parse_listing
from jendhub.hub.store import PackageStore
from jendhub.json import Json
from jendhub.remote.http import http_client

DEV_CORPUS_LIST = Path("bench/dev.json")


class DevError(JendHubError):
    pass


@dataclass(frozen=True)
class PackageBudget:
    count: int


@dataclass(frozen=True)
class DefinitionBudget:
    count: int


type Budget = PackageBudget | DefinitionBudget


@dataclass(frozen=True)
class DevCorpus:
    base_tag: str
    packages: list[PackageRelease]

    def to_json(self) -> str:
        packages = [
            {"hash": package.hash, "name": package.release.name, "version": package.release.version}
            for package in self.packages
        ]
        return json.dumps({"base": self.base_tag, "packages": packages}, indent=2) + "\n"

    @staticmethod
    def from_json(text: str) -> DevCorpus:
        body: Json = json.loads(text)
        match body:
            case {"base": str() as base_tag, "packages": list() as items}:
                packages = [parse_dev_package(item) for item in items]
                return DevCorpus(base_tag, packages)
            case _:
                raise DevError("invalid dev corpus list")

    def top(self, count: int) -> DevCorpus:
        size = len(self.packages)
        if not 0 <= count <= size:
            raise DevError(f"the dev corpus has {size} packages: select 0 to {size}")
        return DevCorpus(self.base_tag, self.packages[:count])


@dataclass(frozen=True)
class Selection:
    corpus: DevCorpus
    definitions: int


def parse_dev_package(item: Json) -> PackageRelease:
    match parse_listing(item):
        case Listing(hash_, Release() as release):
            return PackageRelease(hash_, release)
        case _:
            raise DevError(f"invalid dev corpus package: {item!r}")


def count_definitions(files: dict[str, bytes]) -> int:
    return sum(len(extract(SourceFile(BASE, content.decode()))) for content in files.values())


def budget_used(budget: Budget, packages: int, definitions: int) -> int:
    match budget:
        case PackageBudget():
            return packages
        case DefinitionBudget():
            return definitions


async def select_packages(
    client: httpx.AsyncClient, store: PackageStore, budget: Budget, invalid: set[str]
) -> Selection:
    base_tag = await store.latest_base_tag()
    if base_tag is None:
        raise DevError("the store has no base.bend: run jendhub sync first")
    stored = await store.stored_hashes()
    rejected = await store.rejected_hashes() | invalid
    names: set[str] = set()
    packages: list[PackageRelease] = []
    definitions = 0
    async with aclosing(listings(client, "hot")) as entries:
        async for entry in entries:
            if budget_used(budget, len(packages), definitions) >= budget.count:
                break
            match entry:
                case Listing(hash_, Release(name) as release) if (
                    hash_ not in rejected and name not in names
                ):
                    if hash_ not in stored:
                        raise DevError(f"{hash_} is not in the store: run jendhub sync first")
                    names.add(name)
                    added = count_definitions((await store.package(hash_)).files)
                    if budget_used(budget, len(packages) + 1, definitions + added) <= budget.count:
                        packages.append(PackageRelease(hash_, release))
                        definitions += added
                case _:
                    pass
    return Selection(DevCorpus(base_tag, packages), definitions)


async def select(store: PackageStore, budget: Budget, invalid: set[str]) -> Selection:
    async with http_client() as http:
        return await select_packages(http, store, budget, invalid)


def dev_corpus_list() -> DevCorpus:
    if not DEV_CORPUS_LIST.is_file():
        raise DevError(f"{DEV_CORPUS_LIST} does not exist: run jendhub dev select first")
    return DevCorpus.from_json(DEV_CORPUS_LIST.read_text())


async def open_corpus(store: PackageStore, listed: DevCorpus) -> Corpus:
    stored = await store.stored_hashes()
    missing = [package.hash for package in listed.packages if package.hash not in stored]
    if missing:
        raise DevError(
            f"{len(missing)} packages of the dev corpus are not stored: run jendhub sync"
        )
    if not store.library.base_source(listed.base_tag).is_file():
        raise DevError(f"the store has no base.bend {listed.base_tag}: run jendhub sync")
    return Corpus(store.library, listed.base_tag, listed.packages)
