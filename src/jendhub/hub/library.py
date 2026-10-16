import shutil
from dataclasses import dataclass
from pathlib import Path

from jendhub.hub.api import Package
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore

REFRESH_BATCH = 100


def write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_bytes(content)
    staged.replace(path)


@dataclass(frozen=True)
class Library:
    root: Path

    @property
    def lib(self) -> Path:
        return self.root / "lib"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def cursor(self) -> Path:
        return self.root / "sequence"

    def base_source(self, tag: str) -> Path:
        return self.root / "base" / tag / "base.bend"

    def read_package(self, hash_: str) -> dict[str, bytes]:
        root = self.lib / hash_
        if not root.is_dir():
            raise FileNotFoundError(root)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def store_package(self, package: Package) -> None:
        listing = package.listing
        staged = self.staging / listing.hash
        shutil.rmtree(staged, ignore_errors=True)
        for path, content in package.files.items():
            target = staged / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        staged.mkdir(parents=True, exist_ok=True)
        self.lib.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.lib / listing.hash, ignore_errors=True)
        staged.replace(self.lib / listing.hash)
        if listing.release is not None:
            name_file = self.lib / "names" / str(listing.release)
            write_atomically(name_file, f"{listing.hash}\n".encode())

    def store_base(self, base: Base) -> None:
        write_atomically(self.base_source(base.tag), base.source)

    def sequence(self) -> int:
        return int(self.cursor.read_text()) if self.cursor.is_file() else 0

    def set_sequence(self, sequence: int) -> None:
        write_atomically(self.cursor, f"{sequence}\n".encode())


async def refresh(library: Library, store: PackageStore) -> int:
    added = 0
    while found := await store.packages_after(library.sequence(), REFRESH_BATCH):
        for stored in found:
            library.store_package(stored.package)
        library.set_sequence(found[-1].sequence)
        added += len(found)
    return added


async def ensure_base(library: Library, store: PackageStore, tag: str) -> Path:
    path = library.base_source(tag)
    if not path.is_file():
        library.store_base(await store.base(tag))
    return path
