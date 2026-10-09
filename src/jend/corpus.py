import hashlib
from dataclasses import dataclass

from jend import base
from jend.mirror import Base, File, Mirror, Origin, Package, Tld, files, label, origin_hash

HOTTEST_PACKAGES = 100


@dataclass(frozen=True)
class Entry:
    origin: Origin
    rank: int
    file: File
    tld: Tld

    @property
    def is_base(self) -> bool:
        return isinstance(self.origin, Base)

    @property
    def package_hash(self) -> str:
        return origin_hash(self.origin)

    @property
    def package_title(self) -> str:
        return base.NAME if isinstance(self.origin, Base) else label(self.origin)

    @property
    def description(self) -> str:
        return base.DESCRIPTION if isinstance(self.origin, Base) else self.origin.description

    @property
    def content_key(self) -> str:
        text = f"{self.tld.declaration}\0{self.tld.full_doc}"
        return hashlib.sha256(text.encode()).hexdigest()[:32]


def select(mirror: Mirror, limit: int = HOTTEST_PACKAGES) -> list[Origin]:
    latest: dict[str, Package] = {}
    for package in mirror.packages:
        key = package.hash if package.label is None else package.label.name
        current = latest.get(key)
        if current is None or package.published > current.published:
            latest[key] = package
    hottest = sorted(latest.values(), key=lambda package: package.hot, reverse=True)
    return [mirror.base, *hottest[:limit]]


def entries(origins: list[Origin]) -> list[Entry]:
    return [
        Entry(origin, rank, file, tld)
        for rank, origin in enumerate(origins)
        for file in files(origin)
        for tld in file.tlds
    ]
