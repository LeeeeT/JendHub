import shutil
from dataclasses import dataclass
from pathlib import Path

from jendhub.hub.api import Package
from jendhub.hub.base import Base


def write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_bytes(content)
    staged.replace(path)


def file_paths(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@dataclass(frozen=True)
class Library:
    root: Path

    @property
    def lib(self) -> Path:
        return self.root / "lib"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    def base_directory(self, tag: str) -> Path:
        return self.root / "base" / tag

    def base_source(self, tag: str) -> Path:
        return self.base_directory(tag) / "base.bend"

    def package_directory(self, hash_: str) -> Path:
        return self.lib / hash_

    def read_package(self, hash_: str) -> dict[str, bytes]:
        root = self.package_directory(hash_)
        if not root.is_dir():
            raise FileNotFoundError(root)
        return {path: (root / path).read_bytes() for path in file_paths(root)}

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
        shutil.rmtree(self.package_directory(listing.hash), ignore_errors=True)
        staged.replace(self.package_directory(listing.hash))
        if listing.release is not None:
            name_file = self.lib / "names" / str(listing.release)
            write_atomically(name_file, f"{listing.hash}\n".encode())

    def store_base(self, base: Base) -> None:
        write_atomically(self.base_source(base.tag), base.source)
