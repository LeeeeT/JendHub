from dataclasses import dataclass

from jendhub.corpus.definitions import BASE, Definition, Origin, SourceFile, extract
from jendhub.hub.api import PackageRelease
from jendhub.hub.library import Library


@dataclass(frozen=True)
class Corpus:
    library: Library
    base_tag: str
    packages: list[PackageRelease]


def source_files(corpus: Corpus) -> list[SourceFile]:
    base = corpus.library.base_source(corpus.base_tag)
    files = [SourceFile(BASE, base.read_text(encoding="utf-8"))]
    for package in sorted(corpus.packages, key=lambda package: str(package.release)):
        for path, content in sorted(corpus.library.read_package(package.hash).items()):
            origin = Origin(package.release, path.removesuffix(".bend"))
            files.append(SourceFile(origin, content.decode(errors="replace")))
    return files


def corpus_definitions(corpus: Corpus) -> list[Definition]:
    return [definition for file in source_files(corpus) for definition in extract(file)]


def corpus_text(corpus: Corpus) -> str:
    sections = [
        f"===== {file.origin.label} =====\n{file.source.rstrip()}\n"
        for file in source_files(corpus)
    ]
    return "\n".join(sections)
