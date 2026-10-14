import json
import shutil
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

import httpx

from jendhub.base import fetch_base
from jendhub.definitions import BASE, Corpus, SourceFile, extract
from jendhub.errors import JendHubError
from jendhub.http import http_client
from jendhub.hub import Json, Listing, Package, Release, listings, parse_listing
from jendhub.mirror import Mirror


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
class DevPackage:
    hash: str
    release: Release


@dataclass(frozen=True)
class DevCorpus:
    base_tag: str
    packages: list[DevPackage]

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


def parse_dev_package(item: Json) -> DevPackage:
    match parse_listing(item):
        case Listing(hash_, Release() as release):
            return DevPackage(hash_, release)
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


async def select_packages(client: httpx.AsyncClient, full: Mirror, budget: Budget) -> Selection:
    base_tag = full.stored_base_tag()
    if base_tag is None:
        raise DevError("the full corpus has no base.bend: run jendhub sync first")
    stored = full.hashes()
    rejected = full.rejected_hashes()
    names: set[str] = set()
    packages: list[DevPackage] = []
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
                        raise DevError(f"{hash_} is not in the full corpus: run jendhub sync first")
                    names.add(name)
                    added = count_definitions(full.read_package(hash_))
                    if budget_used(budget, len(packages) + 1, definitions + added) <= budget.count:
                        packages.append(DevPackage(hash_, release))
                        definitions += added
                case _:
                    pass
    return Selection(DevCorpus(base_tag, packages), definitions)


async def build_corpus(
    client: httpx.AsyncClient, full: Mirror, dev_root: Path, corpus: DevCorpus
) -> None:
    staging = dev_root.with_name(f".{dev_root.name}.staging")
    shutil.rmtree(staging, ignore_errors=True)
    dev = Mirror(staging)
    for package in corpus.packages:
        listing = Listing(package.hash, package.release)
        dev.store_package(Package(listing, full.read_package(package.hash)))
    dev.store_base(await fetch_base(client, corpus.base_tag))
    shutil.rmtree(dev_root, ignore_errors=True)
    staging.replace(dev_root)


def open_corpus(dev: Mirror, listed: DevCorpus) -> Corpus:
    releases = dev.releases()
    if dev.stored_base_tag() != listed.base_tag or any(
        releases.get(str(package.release)) != package.hash for package in listed.packages
    ):
        raise DevError(f"{dev.root} does not match the dev corpus list: run jendhub dev build")
    return Corpus(dev, [package.release for package in listed.packages])


async def select(full: Mirror, budget: Budget) -> Selection:
    async with http_client() as http:
        return await select_packages(http, full, budget)


async def build(full: Mirror, dev_root: Path, corpus: DevCorpus) -> None:
    async with http_client() as http:
        await build_corpus(http, full, dev_root, corpus)
