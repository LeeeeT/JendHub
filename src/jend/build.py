import argparse
import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2
import numpy as np

from jend import corpus, embed, enrich, index, mirror, openrouter
from jend.corpus import Entry
from jend.enrich import Enrichment
from jend.index import Record, Vectors

SIGNATURE_TEXT_CHARS = 400
CACHE = "cache"
ENRICHMENT = "enrichment.jsonl"


@dataclass(frozen=True)
class Document:
    key: str
    entries: tuple[Entry, ...]
    enrichment: Enrichment | None

    @property
    def entry(self) -> Entry:
        return self.entries[0]

    def signature_text(self) -> str:
        definition = self.entry.definition
        return f"{definition.name}\n{definition.signature[:SIGNATURE_TEXT_CHARS]}"

    def text(self) -> str:
        definition = self.entry.definition
        parts = [definition.name, definition.doc]
        if self.enrichment is not None:
            parts += [
                self.enrichment.summary,
                *self.enrichment.queries,
                " ".join(self.enrichment.keywords),
            ]
        parts += [
            definition.signature[:800],
            self.entry.package_label,
            self.entry.package.description[:200],
        ]
        return "\n".join(part for part in parts if part)

    def record(self) -> Record:
        entry = self.entry
        package = entry.package
        return Record(
            key=self.key,
            name=entry.definition.name,
            kind=entry.definition.kind,
            signature=entry.definition.signature,
            doc=entry.definition.doc or None,
            line=entry.definition.line,
            path=entry.path,
            package_hash=package.hash,
            package_name=package.name,
            package_version=package.version,
            package_rank=entry.rank,
            is_base=package.is_base,
            role=None if self.enrichment is None else self.enrichment.role,
            summary=None if self.enrichment is None else self.enrichment.summary,
        )


def _entries(data: Path) -> list[Entry]:
    return corpus.entries(corpus.select(mirror.load(data / "mirror.json")))


def _documents(data: Path) -> list[Document]:
    enrichments = enrich.load(data / CACHE / ENRICHMENT)
    grouped: dict[str, list[Entry]] = {}
    for entry in _entries(data):
        grouped.setdefault(entry.content_key, []).append(entry)
    return [
        Document(
            key,
            tuple(sorted(group, key=lambda entry: entry.rank)),
            enrichments.get(key),
        )
        for key, group in grouped.items()
    ]


def _texts(documents: Sequence[Document]) -> dict[str, list[str]]:
    return {
        index.TEXT: [document.text() for document in documents],
        index.SIGNATURE: [document.signature_text() for document in documents],
    }


def _vectors(directory: Path, name: str, texts: Sequence[str]) -> Vectors:
    keys, vectors = embed.Store(directory, name).load()
    row = {key: position for position, key in enumerate(keys)}
    wanted = [embed.vector_key(text) for text in texts]
    missing = sum(key not in row for key in wanted)
    if missing:
        raise ValueError(f"{missing} documents have no {name} vector; run `python -m jend.build`")
    return vectors[[row[key] for key in wanted]]


async def _embed_missing(
    client: httpx2.AsyncClient, directory: Path, name: str, texts: Sequence[str]
) -> None:
    store = embed.Store(directory, name)
    keys, vectors = store.load()
    known = {key: row for row, key in enumerate(keys)}
    wanted = {embed.vector_key(text): text for text in texts}
    missing = [key for key in wanted if key not in known]
    added, cost = await embed.embed(client, [wanted[key] for key in missing])
    rows = {key: vectors[row] for key, row in known.items()}
    rows.update(zip(missing, added))
    store.save(list(wanted), np.stack([rows[key] for key in wanted]))
    print(f"{name} embeddings: {len(missing)} added, ${cost:.4f}")


def compile_index(data: Path) -> None:
    documents = _documents(data)
    texts = _texts(documents)
    keys = [embed.vector_key(text) for name in texts for text in texts[name]]
    identity = hashlib.sha256("\n".join([embed.MODEL, *keys]).encode()).hexdigest()[:16]
    index.write(
        data / index.INDEX,
        [document.record() for document in documents],
        texts[index.TEXT],
        _vectors(data / CACHE, index.TEXT, texts[index.TEXT]),
        _vectors(data / CACHE, index.SIGNATURE, texts[index.SIGNATURE]),
        identity,
    )
    print(f"index {identity}: {len(documents)} documents")


async def build(data: Path, budget: float) -> None:
    report = await enrich.enrich(_entries(data), data / CACHE / ENRICHMENT, budget)
    print(
        f"enrichment: {report.written} written, {report.failed_batches} failed batches,"
        f" {report.skipped_batches} skipped batches, ${report.cost:.4f}"
    )
    texts = _texts(_documents(data))
    async with openrouter.connect() as client:
        for name, values in texts.items():
            await _embed_missing(client, data / CACHE, name, values)
    compile_index(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich and embed the hottest packages, then write the search index."
    )
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--budget", type=float, required=True, help="USD limit for enrichment")
    args = parser.parse_args()
    asyncio.run(build(args.data, args.budget))


if __name__ == "__main__":
    main()
