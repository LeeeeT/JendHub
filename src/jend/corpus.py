import hashlib
from dataclasses import dataclass

from jend.loader import Definition
from jend.mirror import Mirror, Package

HOTTEST_PACKAGES = 100


@dataclass(frozen=True)
class Entry:
    package: Package
    rank: int
    path: str
    definition: Definition

    @property
    def id(self) -> str:
        return f"{self.package.hash}/{self.path}:{self.definition.line}"

    @property
    def package_label(self) -> str:
        if self.package.name is None:
            return self.package.hash
        return f"{self.package.name}@{self.package.version}"

    @property
    def content_key(self) -> str:
        text = f"{self.definition.signature}\0{self.definition.full_doc}"
        return hashlib.sha256(text.encode()).hexdigest()[:32]


def select(mirror: Mirror, limit: int = HOTTEST_PACKAGES) -> list[Package]:
    latest: dict[str, Package] = {}
    for package in mirror.packages:
        key = package.name or package.hash
        current = latest.get(key)
        if current is None or package.published > current.published:
            latest[key] = package
    hottest = sorted(
        latest.values(),
        key=lambda package: float("-inf") if package.hot is None else package.hot,
        reverse=True,
    )
    return [mirror.base, *hottest[:limit]]


def entries(packages: list[Package]) -> list[Entry]:
    return [
        Entry(package, rank, file.path, definition)
        for rank, package in enumerate(packages)
        for file in package.files
        for definition in file.definitions
    ]
