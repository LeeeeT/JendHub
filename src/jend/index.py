import json
import math
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from jend.enrich import Role
from jend.signatures import Kind

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
Vector = npt.NDArray[np.float32]
Vectors = npt.NDArray[np.float32]
Rows = npt.NDArray[np.intp]
Codes = npt.NDArray[np.int8]

BM25_K1 = 1.2
BM25_B = 0.75
CODE_LIMIT = 127
SCAN_ROWS = 256
INDEX = "index"
DATABASE = "index.sqlite"
TEXT = "text"
SIGNATURE = "signature"

SCHEMA = """
create table meta (name text primary key, value text not null) without rowid;
create table documents (
    row integer primary key,
    key text not null,
    name text not null,
    kind text not null,
    signature text not null,
    line integer not null,
    path text not null,
    package_hash text not null,
    package_name text,
    package_version text,
    package_rank integer not null,
    is_base integer not null,
    role text,
    summary text
);
create table postings (term text primary key, rows blob not null, weights blob not null)
    without rowid;
"""


@dataclass(frozen=True)
class Record:
    key: str
    name: str
    kind: Kind
    signature: str
    line: int
    path: str
    package_hash: str
    package_name: str | None
    package_version: str | None
    package_rank: int
    is_base: bool
    role: Role | None
    summary: str | None

    @property
    def package_label(self) -> str:
        if self.package_name is None:
            return self.package_hash
        return f"{self.package_name}@{self.package_version}"


RECORD_COLUMNS = (
    "key",
    "name",
    "kind",
    "signature",
    "line",
    "path",
    "package_hash",
    "package_name",
    "package_version",
    "package_rank",
    "is_base",
    "role",
    "summary",
)
SELECTED = ", ".join(RECORD_COLUMNS)


def _row(record: Record) -> tuple[object, ...]:
    return (
        record.key,
        record.name,
        record.kind.value,
        record.signature,
        record.line,
        record.path,
        record.package_hash,
        record.package_name,
        record.package_version,
        record.package_rank,
        record.is_base,
        None if record.role is None else record.role.value,
        record.summary,
    )


def _record(row: Sequence[Any]) -> Record:
    (
        key,
        name,
        kind,
        signature,
        line,
        path,
        package_hash,
        package_name,
        package_version,
        package_rank,
        is_base,
        role,
        summary,
    ) = row
    return Record(
        key=key,
        name=name,
        kind=Kind(kind),
        signature=signature,
        line=line,
        path=path,
        package_hash=package_hash,
        package_name=package_name,
        package_version=package_version,
        package_rank=package_rank,
        is_base=bool(is_base),
        role=None if role is None else Role(role),
        summary=summary,
    )


def tokens(text: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    return [
        token
        for token in re.findall(r"[a-z]+|\d+", spaced)
        if len(token) > 1 and token not in STOPWORDS
    ]


Posting = tuple[npt.NDArray[np.int32], npt.NDArray[np.float32]]


def postings(texts: Sequence[str]) -> dict[str, Posting]:
    counts = [Counter(tokens(text)) for text in texts]
    lengths = np.array([sum(count.values()) for count in counts], dtype=np.float32)
    average = float(lengths.mean()) if len(counts) else 1.0
    rows: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
    for row, count in enumerate(counts):
        for term, frequency in count.items():
            rows[term].append((row, frequency))
    result: dict[str, Posting] = {}
    for term, pairs in rows.items():
        ids = np.array([row for row, _ in pairs], dtype=np.int32)
        frequency = np.array([value for _, value in pairs], dtype=np.float32)
        idf = math.log(1 + (len(counts) - len(pairs) + 0.5) / (len(pairs) + 0.5))
        norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths[ids] / average)
        weights = idf * frequency * (BM25_K1 + 1) / (frequency + norm)
        result[term] = (ids, weights.astype(np.float32))
    return result


@dataclass(frozen=True)
class Quantized:
    codes: Codes
    scale: Vector

    @staticmethod
    def of(vectors: Vectors) -> "Quantized":
        peak = np.abs(vectors).max(axis=0) if len(vectors) else np.ones(vectors.shape[1])
        scale = np.where(peak > 0, peak / CODE_LIMIT, 1.0).astype(np.float32)
        codes = np.round(vectors / scale).clip(-CODE_LIMIT, CODE_LIMIT).astype(np.int8)
        return Quantized(codes, scale)

    def scores(self, vector: Vector) -> Scores:
        weights = vector * self.scale
        result = np.empty(len(self.codes), dtype=np.float32)
        decoded = np.empty((SCAN_ROWS, self.codes.shape[1]), dtype=np.float32)
        for start in range(0, len(self.codes), SCAN_ROWS):
            block = self.codes[start : start + SCAN_ROWS]
            np.copyto(decoded[: len(block)], block, casting="unsafe")
            np.matmul(decoded[: len(block)], weights, out=result[start : start + len(block)])
        return result

    def row_scores(self, rows: Rows, vector: Vector) -> Scores:
        scores = self.codes[rows].astype(np.float32) @ (vector * self.scale)
        return scores.astype(np.float32, copy=False)

    def save(self, directory: Path, name: str) -> None:
        np.save(directory / f"{name}.codes.npy", self.codes)
        np.save(directory / f"{name}.scale.npy", self.scale)

    @staticmethod
    def load(directory: Path, name: str, mapped: bool) -> "Quantized":
        codes: Codes = np.load(directory / f"{name}.codes.npy", mmap_mode="r" if mapped else None)
        scale: Vector = np.load(directory / f"{name}.scale.npy")
        return Quantized(codes, scale)


def write(
    directory: Path,
    records: Sequence[Record],
    texts: Sequence[str],
    text_vectors: Vectors,
    signature_vectors: Vectors,
    identity: str,
) -> None:
    partial = directory.with_name(directory.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    Quantized.of(text_vectors).save(partial, TEXT)
    Quantized.of(signature_vectors).save(partial, SIGNATURE)
    connection = sqlite3.connect(partial / DATABASE)
    with connection:
        connection.executescript(SCHEMA)
        connection.execute("insert into meta values ('id', ?)", (identity,))
        connection.executemany(
            f"insert into documents (row, {SELECTED})"
            f" values (?, {', '.join('?' * len(RECORD_COLUMNS))})",
            ((row, *_row(record)) for row, record in enumerate(records)),
        )
        connection.executemany(
            "insert into postings values (?, ?, ?)",
            (
                (term, ids.tobytes(), weights.tobytes())
                for term, (ids, weights) in postings(texts).items()
            ),
        )
    connection.execute("vacuum")
    connection.close()
    shutil.rmtree(directory, ignore_errors=True)
    partial.replace(directory)


class Index:
    def __init__(self, directory: Path) -> None:
        uri = f"{(directory / DATABASE).resolve().as_uri()}?immutable=1"
        self.connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        (self.id,) = self.connection.execute("select value from meta where name = 'id'").fetchone()
        self.text = Quantized.load(directory, TEXT, mapped=False)
        self.signature = Quantized.load(directory, SIGNATURE, mapped=True)
        self.size = len(self.text.codes)

    def semantic(self, vector: Vector) -> Scores:
        return self.text.scores(vector)

    def signature_scores(self, rows: Rows, vector: Vector) -> Scores:
        return self.signature.row_scores(rows, vector)

    def lexical(self, query: str) -> Scores:
        scores = np.zeros(self.size, dtype=np.float32)
        for term in sorted(set(tokens(query))):
            found = self.connection.execute(
                "select rows, weights from postings where term = ?", (term,)
            ).fetchone()
            if found is not None:
                ids = np.frombuffer(found[0], dtype=np.int32)
                np.add.at(scores, ids, np.frombuffer(found[1], dtype=np.float32))
        return scores

    def _select(self, columns: str, rows: Rows) -> list[tuple[Any, ...]]:
        wanted: list[int] = rows.tolist()
        found: dict[int, tuple[Any, ...]] = {
            row[0]: row[1:]
            for row in self.connection.execute(
                f"select row, {columns} from documents"
                " where row in (select value from json_each(?))",
                (json.dumps(wanted),),
            )
        }
        return [found[row] for row in wanted]

    def names(self, rows: Rows) -> list[str]:
        return [name for (name,) in self._select("name", rows)]

    def keys(self, rows: Rows) -> list[str]:
        return [key for (key,) in self._select("key", rows)]

    def records(self, rows: Rows) -> list[Record]:
        return [_record(row) for row in self._select(SELECTED, rows)]

    def scan(self) -> Iterator[Record]:
        for row in self.connection.execute(f"select {SELECTED} from documents order by row"):
            yield _record(row)

    def close(self) -> None:
        self.connection.close()
