import shutil
from dataclasses import dataclass
from pathlib import Path

from jendhub.base import Base
from jendhub.hub import HASH, Package, Rejection


def write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_bytes(content)
    staged.replace(path)


@dataclass(frozen=True)
class Mirror:
    root: Path

    @property
    def lib(self) -> Path:
        return self.root / "lib"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def base_source(self) -> Path:
        return self.root / "base" / "base.bend"

    @property
    def base_tag(self) -> Path:
        return self.root / "base" / "tag"

    @property
    def rejected(self) -> Path:
        return self.root / "rejected"

    def hashes(self) -> set[str]:
        if not self.lib.is_dir():
            return set()
        return {entry.name for entry in self.lib.iterdir() if HASH.fullmatch(entry.name)}

    def releases(self) -> dict[str, str]:
        names = self.lib / "names"
        if not names.is_dir():
            return {}
        return {entry.name: entry.read_text().strip() for entry in sorted(names.iterdir())}

    def read_package(self, hash_: str) -> dict[str, bytes]:
        root = self.lib / hash_
        if not root.is_dir():
            raise FileNotFoundError(root)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def rejected_hashes(self) -> set[str]:
        if not self.rejected.is_dir():
            return set()
        return {entry.name for entry in self.rejected.iterdir() if HASH.fullmatch(entry.name)}

    def reject(self, rejection: Rejection) -> None:
        write_atomically(self.rejected / rejection.hash, f"{rejection.reason}\n".encode())

    def store_package(self, package: Package) -> None:
        listing = package.listing
        if listing.release is not None:
            name_file = self.lib / "names" / str(listing.release)
            write_atomically(name_file, f"{listing.hash}\n".encode())
        staged = self.staging / listing.hash
        shutil.rmtree(staged, ignore_errors=True)
        for path, content in package.files.items():
            target = staged / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        staged.mkdir(parents=True, exist_ok=True)
        self.lib.mkdir(parents=True, exist_ok=True)
        staged.replace(self.lib / listing.hash)

    def stored_base_tag(self) -> str | None:
        if not self.base_tag.is_file():
            return None
        return self.base_tag.read_text().strip()

    def store_base(self, base: Base) -> None:
        write_atomically(self.base_source, base.source)
        write_atomically(self.base_tag, f"{base.tag}\n".encode())
