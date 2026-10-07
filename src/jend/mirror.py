import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from jend import base, hub
from jend.signatures import Definition, extract

MIRROR = "mirror.json"
FILES = "files"


class File(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    definitions: tuple[Definition, ...]
    unparsed_lines: tuple[int, ...]


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


def build(release: base.Release, listings: list[hub.Listing], cache: Path) -> Mirror:
    return Mirror(
        base=Package(
            hash=release.sha,
            name=base.NAME,
            version=release.sha[:12],
            description=base.DESCRIPTION,
            published=release.published,
            hot=None,
            files=(_file(base.PATH, release.source),),
        ),
        packages=tuple(_package(listing, cache) for listing in listings),
    )


def _file(path: str, source: Path) -> File:
    extraction = extract(source.read_text(encoding="utf-8"))
    return File(
        path=path,
        definitions=extraction.definitions,
        unparsed_lines=extraction.unparsed_lines,
    )


def _package(listing: hub.Listing, cache: Path) -> Package:
    return Package(
        hash=listing.hash,
        name=listing.name,
        version=listing.version,
        description=listing.desc,
        published=datetime.fromtimestamp(listing.ts / 1000, UTC),
        hot=listing.hot,
        files=tuple(
            _file(path, hub.cached_path(cache, listing.hash, path)) for path in listing.bend_files()
        ),
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
        description="Mirror BendHub and extract definition signatures."
    )
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    args = parser.parse_args()
    mirror = asyncio.run(sync(args.data))
    packages = (mirror.base, *mirror.packages)
    files = [file for package in packages for file in package.files]
    definitions = sum(len(file.definitions) for file in files)
    unparsed = sum(len(file.unparsed_lines) for file in files)
    print(
        f"Base {mirror.base.version} and {len(mirror.packages)} packages, {len(files)} files,"
        f" {definitions} definitions, {unparsed} declarations not parsed"
    )


if __name__ == "__main__":
    main()
