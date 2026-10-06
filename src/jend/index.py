import argparse
import asyncio
import hashlib
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2
import numpy as np
import numpy.typing as npt

from jend import corpus, embed, enrich, mirror, openrouter
from jend.corpus import Entry
from jend.enrich import Enrichment

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
    ]
)

Scores = npt.NDArray[np.float32]
BM25_K1 = 1.2
BM25_B = 0.75
SIGNATURE_TEXT_CHARS = 400


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
            parts += [self.enrichment.summary, *self.enrichment.queries]
        parts += [
            definition.signature[:800],
            self.entry.package_label,
            self.entry.package.description[:200],
        ]
        return "\n".join(part for part in parts if part)


def tokens(text: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    return [
        token
        for token in re.findall(r"[a-z]+|\d+", spaced)
        if len(token) > 1 and token not in STOPWORDS
    ]


class Bm25:
    def __init__(self, texts: Sequence[str]) -> None:
        counts = [Counter(tokens(text)) for text in texts]
        lengths = np.array([sum(count.values()) for count in counts], dtype=np.float32)
        average = float(lengths.mean()) if len(counts) else 1.0
        postings: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
        for row, count in enumerate(counts):
            for term, frequency in count.items():
                postings[term].append((row, frequency))
        self.size = len(counts)
        self.postings: dict[str, tuple[npt.NDArray[np.int32], npt.NDArray[np.float32]]] = {}
        for term, rows in postings.items():
            ids = np.array([row for row, _ in rows], dtype=np.int32)
            frequency = np.array([value for _, value in rows], dtype=np.float32)
            idf = math.log(1 + (self.size - len(rows) + 0.5) / (len(rows) + 0.5))
            norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths[ids] / average)
            weights = idf * frequency * (BM25_K1 + 1) / (frequency + norm)
            self.postings[term] = (ids, weights.astype(np.float32))

    def scores(self, query: str) -> Scores:
        scores = np.zeros(self.size, dtype=np.float32)
        for term in sorted(set(tokens(query))):
            posting = self.postings.get(term)
            if posting is not None:
                np.add.at(scores, posting[0], posting[1])
        return scores


@dataclass(frozen=True)
class Index:
    documents: tuple[Document, ...]
    text_vectors: embed.Vectors
    signature_vectors: embed.Vectors
    bm25: Bm25
    id: str


def _documents(data: Path) -> list[Document]:
    entries = corpus.entries(corpus.select(mirror.load(data / "mirror.json")))
    enrichments = enrich.load(data / "index" / "enrichment.jsonl")
    grouped: dict[str, list[Entry]] = {}
    for entry in entries:
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
        "text": [document.text() for document in documents],
        "signature": [document.signature_text() for document in documents],
    }


def _vectors(directory: Path, name: str, texts: Sequence[str]) -> embed.Vectors:
    keys, vectors = embed.Store(directory, name).load()
    row = {key: position for position, key in enumerate(keys)}
    wanted = [embed.vector_key(text) for text in texts]
    missing = sum(key not in row for key in wanted)
    if missing:
        raise ValueError(f"{missing} documents have no {name} vector; run `python -m jend.index`")
    return vectors[[row[key] for key in wanted]]


def load(data: Path) -> Index:
    documents = _documents(data)
    texts = _texts(documents)
    keys = [embed.vector_key(text) for name in ("text", "signature") for text in texts[name]]
    identity = hashlib.sha256("\n".join([embed.MODEL, *keys]).encode()).hexdigest()[:16]
    return Index(
        tuple(documents),
        _vectors(data / "index", "text", texts["text"]),
        _vectors(data / "index", "signature", texts["signature"]),
        Bm25(texts["text"]),
        identity,
    )


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


async def build(data: Path, budget: float) -> None:
    entries = corpus.entries(corpus.select(mirror.load(data / "mirror.json")))
    report = await enrich.enrich(entries, data / "index" / "enrichment.jsonl", budget)
    print(
        f"enrichment: {report.written} written, {report.failed_batches} failed batches,"
        f" {report.skipped_batches} skipped batches, ${report.cost:.4f}"
    )
    texts = _texts(_documents(data))
    async with openrouter.connect() as client:
        for name, values in texts.items():
            await _embed_missing(client, data / "index", name, values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich and embed the hottest packages.")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--budget", type=float, required=True, help="USD limit for enrichment")
    args = parser.parse_args()
    asyncio.run(build(args.data, args.budget))


if __name__ == "__main__":
    main()
