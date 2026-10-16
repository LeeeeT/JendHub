import itertools
from dataclasses import dataclass

from jendhub.corpus.corpus import Corpus, source_files
from jendhub.corpus.definitions import Definition, Origin, SourceFile, extract

BEFORE = 40
TARGETS = 40
AFTER = 20
CONTEXT_LIMIT = 30_000


@dataclass(frozen=True)
class Window:
    origin: Origin
    first: int
    lines: list[str]
    targets: list[Definition]


def windows(file: SourceFile) -> list[Window]:
    definitions = extract(file)
    lines = file.source.splitlines()
    offsets = [0, *itertools.accumulate(len(line) + 1 for line in lines)]
    found: list[Window] = []
    for index in range(0, len(definitions), TARGETS):
        targets = definitions[index : index + TARGETS]
        before = definitions[max(0, index - BEFORE) : index]
        after = definitions[index + TARGETS : index + TARGETS + AFTER]
        while before and offsets[targets[0].start] - offsets[before[0].start] > CONTEXT_LIMIT:
            before = before[1:]
        while after and offsets[after[-1].end] - offsets[targets[-1].end] > CONTEXT_LIMIT:
            after = after[:-1]
        first = (before or targets)[0].start
        end = (after or targets)[-1].end
        found.append(Window(file.origin, first, lines[first:end], targets))
    return found


def corpus_windows(corpus: Corpus) -> list[Window]:
    return [window for file in source_files(corpus) for window in windows(file)]
