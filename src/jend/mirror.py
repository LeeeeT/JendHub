import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from jend import base, hub
from jend.loader import Definition, FileKey, Import, Library, load_all

MIRROR = "mirror.json"
FILES = "files"


class File(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    error: str | None
    imports: tuple[Import, ...]
    definitions: tuple[Definition, ...]


class Package(BaseModel):
    model_config = ConfigDict(frozen=True)

    hash: str
    name: str | None
    version: str | None
    description: str
    published: datetime
    hot: float | None
    files: tuple[File, ...]

    @property
    def is_base(self) -> bool:
        return self.hot is None


class Mirror(BaseModel):
    model_config = ConfigDict(frozen=True)

    base: Package
    packages: tuple[Package, ...]


def extract(library: Library, keys: Sequence[FileKey]) -> dict[FileKey, File]:
    loader = load_all(library, list(keys))
    graph = loader.graph()
    files: dict[FileKey, File] = {}
    for key in keys:
        module = loader.modules.get(key)
        if module is None:
            files[key] = File(path=key[1], error=loader.errors[key], imports=(), definitions=())
        else:
            definitions = loader.definitions(key, graph)
            files[key] = File(
                path=key[1], error=None, imports=module.imports, definitions=definitions
            )
    return files


def build(release: base.Release, listings: list[hub.Listing], cache: Path) -> Mirror:
    base_key = (release.sha, base.PATH)
    keys = [
        base_key,
        *((listing.hash, path) for listing in listings for path in listing.bend_files()),
    ]
    sources = {key: hub.cached_path(cache, *key).read_text(encoding="utf-8") for key in keys}
    names = {
        f"{listing.name}@{listing.version}": listing.hash
        for listing in listings
        if listing.name is not None and listing.version is not None
    }
    files = extract(Library(sources, names, base_key), keys)
    return Mirror(
        base=Package(
            hash=release.sha,
            name=base.NAME,
            version=release.sha[:12],
            description=base.DESCRIPTION,
            published=release.published,
            hot=None,
            files=(files[base_key],),
        ),
        packages=tuple(_package(listing, files) for listing in listings),
    )


def _package(listing: hub.Listing, files: dict[FileKey, File]) -> Package:
    return Package(
        hash=listing.hash,
        name=listing.name,
        version=listing.version,
        description=listing.desc,
        published=datetime.fromtimestamp(listing.ts / 1000, UTC),
        hot=listing.hot,
        files=tuple(files[(listing.hash, path)] for path in listing.bend_files()),
    )


def load(path: Path) -> Mirror:
    return Mirror.model_validate_json(path.read_bytes())


async def sync(data: Path) -> Mirror:
    release = await base.fetch(data / FILES)
    async with hub.connect() as client:
        listings = await hub.fetch_listings(client)
        await hub.download(client, listings, data / FILES)
    mirror = build(release, listings, data / FILES)
    target = data / MIRROR
    partial = target.with_name(target.name + ".partial")
    partial.write_text(mirror.model_dump_json(), encoding="utf-8")
    partial.replace(target)
    return mirror


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mirror BendHub and read its definitions with Bend's own parser."
    )
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    args = parser.parse_args()
    mirror = asyncio.run(sync(args.data))
    packages = (mirror.base, *mirror.packages)
    files = [file for package in packages for file in package.files]
    definitions = sum(len(file.definitions) for file in files)
    rejected = sum(file.error is not None for file in files)
    print(
        f"Base {mirror.base.version} and {len(mirror.packages)} packages, {len(files)} files,"
        f" {definitions} definitions, {rejected} files that Bend rejects"
    )


if __name__ == "__main__":
    main()
