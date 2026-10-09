import argparse
import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jend import base, hub
from jend.loader import BASE, FileKey, Library, Module, load_all
from jend.parser import Declaration, Tag, module_of

MIRROR = "mirror.json"
FILES = "files"


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class BaseFile:
    commit: str


@dataclass(frozen=True)
class PackageFile:
    package: str
    path: str


FileRef = BaseFile | PackageFile


@dataclass(frozen=True)
class Key:
    file: FileRef
    name: str

    def text(self) -> str:
        if isinstance(self.file, BaseFile):
            return self.name
        return f"{self.file.package}/{self.file.path.removesuffix('.bend')}:{self.name}"


class Named(Model):
    name: str
    version: str


class Import(Model):
    alias: str
    file: PackageFile


class Fill(Model):
    file: FileRef
    doc: str
    code: str


class Def(Model):
    tag: Literal[Tag.DEF] = Tag.DEF
    signature: str


class Adt(Model):
    tag: Literal[Tag.ADT] = Tag.ADT


class Law(Model):
    tag: Literal[Tag.LAW] = Tag.LAW
    fill: Fill | None


Kind = Annotated[Def | Adt | Law, Field(discriminator="tag")]


class Tld(Model):
    name: str
    doc: str
    code: str
    refs: tuple[Key, ...]
    kind: Kind

    @property
    def declaration(self) -> str:
        return self.kind.signature if isinstance(self.kind, Def) else self.code

    @property
    def full_doc(self) -> str:
        fill = self.kind.fill if isinstance(self.kind, Law) else None
        docs = (doc_text(self.doc), "" if fill is None else doc_text(fill.doc))
        return " ".join(doc for doc in docs if doc)


class File(Model):
    path: str
    text: str
    imports: tuple[Import, ...]
    tlds: tuple[Tld, ...]


class Package(Model):
    hash: str
    label: Named | None
    description: str
    published: datetime
    hot: float
    files: tuple[File, ...]


class Base(Model):
    commit: str
    file: File


class Mirror(Model):
    base: Base
    packages: tuple[Package, ...]


Origin = Base | Package


def doc_text(doc: str) -> str:
    if not doc:
        return ""
    return " ".join(line.lstrip("#").strip() for line in doc.split("\n"))


def files(origin: Origin) -> tuple[File, ...]:
    return (origin.file,) if isinstance(origin, Base) else origin.files


def origin_hash(origin: Origin) -> str:
    return origin.commit if isinstance(origin, Base) else origin.hash


def label(origin: Origin) -> str:
    if isinstance(origin, Base):
        return origin.commit
    if origin.label is None:
        return origin.hash
    return f"{origin.label.name}@{origin.label.version}"


def file_ref(origin: Origin, file: File) -> FileRef:
    if isinstance(origin, Base):
        return BaseFile(commit=origin.commit)
    return PackageFile(package=origin.hash, path=file.path)


def key_of(origin: Origin, file: File, tld: Tld) -> Key:
    return Key(file=file_ref(origin, file), name=tld.name)


def extract(library: Library, keys: Sequence[FileKey], commit: str) -> dict[FileKey, File]:
    loader = load_all(library, list(keys))
    refs = loader.references()
    fills: dict[str, Fill] = {}
    for module in loader.modules.values():
        lines = module.text.split("\n")
        for declaration in module.declarations:
            if declaration.fills:
                piece = _piece(module, lines, declaration)
                fills[declaration.key] = Fill(file=_ref(module.ns, commit), **piece)
    return {
        key: _file(key[1], library.sources[key], loader.modules[key], refs, fills, commit)
        for key in keys
        if key in loader.modules
    }


def _file(
    path: str,
    text: str,
    module: Module,
    refs: dict[str, set[str]],
    fills: dict[str, Fill],
    commit: str,
) -> File:
    lines = module.text.split("\n")
    return File(
        path=path,
        text=_lf(text),
        imports=tuple(
            Import(alias=alias, file=_package_file(target))
            for alias, target in module.aliases.items()
        ),
        tlds=tuple(
            _tld(module, lines, declaration, refs, fills, commit)
            for declaration in module.declarations
            if not declaration.fills
        ),
    )


def _tld(
    module: Module,
    lines: list[str],
    declaration: Declaration,
    refs: dict[str, set[str]],
    fills: dict[str, Fill],
    commit: str,
) -> Tld:
    kind: Def | Adt | Law
    match declaration.tag:
        case Tag.DEF:
            header = module.text[declaration.start : declaration.header_end]
            kind = Def(signature=_collapse(_strip_comments(header)))
        case Tag.ADT:
            kind = Adt()
        case Tag.LAW:
            kind = Law(fill=fills.get(declaration.key))
    used = sorted(refs.get(declaration.key, ()))
    return Tld(
        name=declaration.name,
        refs=tuple(_key(text, commit) for text in used),
        kind=kind,
        **_piece(module, lines, declaration),
    )


def _piece(module: Module, lines: list[str], declaration: Declaration) -> dict[str, str]:
    start = module.text.count("\n", 0, declaration.start)
    first = start
    while first > 0 and lines[first - 1].startswith("#"):
        first -= 1
    return {
        "doc": "\n".join(line.removesuffix("\r") for line in lines[first:start]),
        "code": _lf(module.text[declaration.start : declaration.end]),
    }


def _key(text: str, commit: str) -> Key:
    return Key(file=_ref(module_of(text), commit), name=text.rpartition(":")[2])


def _ref(module: str, commit: str) -> FileRef:
    return BaseFile(commit=commit) if module == BASE else _package_file(module)


def _package_file(module: str) -> PackageFile:
    package, _, path = module.partition("/")
    return PackageFile(package=package, path=f"{path}.bend")


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _strip_comments(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "'\"":
            end = index + 1
            while end < len(text) and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            out.append(text[index : end + 1])
            index = end + 1
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _collapse(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([(\[{])\s", r"\1", text)
    return re.sub(r"\s([)\]}])", r"\1", text).strip()


def build(commit: str, listings: list[hub.Listing], cache: Path) -> Mirror:
    base_key = (commit, base.PATH)
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
    files = extract(Library(sources, names, base_key), keys, commit)
    packages = (_package(listing, files) for listing in listings)
    return Mirror(
        base=Base(commit=commit, file=files[base_key]),
        packages=tuple(package for package in packages if package.files),
    )


def _package(listing: hub.Listing, files: dict[FileKey, File]) -> Package:
    name, version = listing.name, listing.version
    return Package(
        hash=listing.hash,
        label=None if name is None or version is None else Named(name=name, version=version),
        description=listing.desc,
        published=datetime.fromtimestamp(listing.ts / 1000, UTC),
        hot=listing.hot,
        files=tuple(
            files[(listing.hash, path)]
            for path in listing.bend_files()
            if (listing.hash, path) in files
        ),
    )


def load(path: Path) -> Mirror:
    return Mirror.model_validate_json(path.read_bytes())


async def sync(data: Path) -> Mirror:
    commit = await base.fetch(data / FILES)
    async with hub.connect() as client:
        listings = await hub.fetch_listings(client)
        await hub.download(client, listings, data / FILES)
    mirror = build(commit, listings, data / FILES)
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
    files = [mirror.base.file, *(file for package in mirror.packages for file in package.files)]
    tlds = sum(len(file.tlds) for file in files)
    print(
        f"Base {mirror.base.commit[:12]} and {len(mirror.packages)} packages,"
        f" {len(files)} files that Bend accepts, {tlds} definitions"
    )


if __name__ == "__main__":
    main()
