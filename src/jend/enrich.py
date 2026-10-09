import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter

from jend import openrouter
from jend.corpus import Entry

MODEL = "deepseek/deepseek-v4-flash"
PROVIDER = "open-inference/fp4"
BATCH = 24
FILE_NAMES = 120
REQUESTS_IN_FLIGHT = 48
SIGNATURE_CHARS = 1500


class Role(StrEnum):
    API = "api"
    HELPER = "helper"
    LOCAL = "local"
    TEST = "test"


INSTRUCTIONS = """\
You write search index entries for definitions from Bend packages. Bend is a \
functional language with Python-like syntax and dependent types.

Syntax: `def f(x: A) -> B` is a function. `type T is Data:` declares a datatype \
with one constructor on each line. `law name:` states a theorem with `for` \
binders; a def with the same name proves it. `+x` is a reusable parameter, `-A` \
an erased type-level parameter, `~f` a compile-time template argument. `&1` and \
`&2` are quantities. `{a == b : T}` is an equality type, that is a proof. \
`A & B` is a pair. `IO(T)` is an effectful action. `Maybe`, `Result`, `List`, \
`Array` and `Map` are the usual types.

You get the package, its description, the file, the names of all definitions \
in the file, and some definitions of the file. For each of these definitions, \
write:
- role: what a user of the package does with the definition. One of:
  - api: a user imports it for its own purpose: an entry point of the package, \
a public type, or a law that the package states for its users.
  - helper: a step of another definition in the file: a loop, a state, a case, \
a continuation, an accumulator, a table entry, or a part of a parser or encoder. \
Its name often extends the name of the definition that it serves.
  - local: a small general utility that the file keeps for its own code, often \
a copy of a Base function, or a function that works only on the private data of \
the package.
  - test: a definition in a test, spec, proof, example, benchmark, conformance \
or usage file, or a lemma that one proof keeps for its own use.
  The file path decides first: a definition in a file or folder whose name \
contains test, spec, proof, example, bench, conformance or usage has the role \
test, also when it looks like an entry point. Exception: laws and lemmas of a \
shared lemma library (for example a folder proofs/lib/) are api.
  For a definition outside such files, decide between api, helper and local from \
the names of the other definitions in the file: when the file has a definition \
that this one serves (for example gunzip for gz.body), it is a helper.
- summary: one plain English sentence of at most 25 words about what the \
definition does or states. Use the words of the problem domain. Do not repeat \
the signature. For a helper, name the definition that it serves and the step \
that it performs.
- queries: for an api definition, 3 short search queries (3 to 8 words each) \
that a developer who needs it would type; prefer words that are not in its name. \
For an api law, write the fact in words and as a short formula, for example \
"adding zero leaves a number unchanged" and "x + 0 == x". For any other role, \
an empty list.
- keywords: for an api definition, 2 or 3 single words or standard names that a \
developer would type alone to find it, for example hash, checksum, crc32, base64, \
sort. For any other role, an empty list.

Answer with one item for each input id."""

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "role": {"type": "string", "enum": [role.value for role in Role]},
                    "summary": {"type": "string"},
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "role", "summary", "queries", "keywords"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class Enrichment(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    role: Role
    summary: str
    queries: tuple[str, ...]
    keywords: tuple[str, ...]
    model: str
    provider: str | None


class _Item(BaseModel):
    id: str
    role: Role
    summary: str
    queries: list[str]
    keywords: list[str]


class _Items(BaseModel):
    items: list[_Item]


_ANSWER: TypeAdapter[_Items | list[_Item]] = TypeAdapter(_Items | list[_Item])


class _Message(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _Message


class _Usage(BaseModel):
    cost: float


class _Completion(BaseModel):
    choices: list[_Choice]
    usage: _Usage


@dataclass
class Report:
    written: int = 0
    failed_batches: int = 0
    skipped_batches: int = 0
    cost: float = 0.0


def load(path: Path) -> dict[str, Enrichment]:
    if not path.exists():
        return {}
    records = (
        Enrichment.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    return {record.key: record for record in records}


def batches(entries: Iterable[Entry], done: set[str]) -> list[list[Entry]]:
    by_file: dict[tuple[str, str], list[Entry]] = {}
    scheduled = set(done)
    for entry in entries:
        if entry.content_key in scheduled:
            continue
        scheduled.add(entry.content_key)
        by_file.setdefault((entry.package_hash, entry.file.path), []).append(entry)
    return [
        group[start : start + BATCH]
        for group in by_file.values()
        for start in range(0, len(group), BATCH)
    ]


async def enrich(
    entries: Iterable[Entry],
    store: Path,
    budget: float,
    model: str = MODEL,
    provider: str = PROVIDER,
) -> Report:
    done = set(load(store))
    work = batches(entries, done)
    report = Report()
    gate = asyncio.Semaphore(REQUESTS_IN_FLIGHT)
    store.parent.mkdir(parents=True, exist_ok=True)
    async with openrouter.connect() as client:
        with store.open("a", encoding="utf-8") as sink:

            async def run(batch: list[Entry]) -> None:
                async with gate:
                    if report.cost >= budget:
                        report.skipped_batches += 1
                        return
                    try:
                        records, cost = await _ask(client, model, provider, batch)
                    except (httpx2.HTTPError, ValueError):
                        report.failed_batches += 1
                        return
                report.cost += cost
                report.written += len(records)
                sink.writelines(record.model_dump_json() + "\n" for record in records)
                sink.flush()

            await asyncio.gather(*(run(batch) for batch in work))
    return report


def _file_names(entry: Entry) -> list[str]:
    return [tld.name for tld in entry.file.tlds][:FILE_NAMES]


def request(model: str, provider: str, batch: list[Entry]) -> dict[str, object]:
    first = batch[0]
    prompt = {
        "package": first.package_title,
        "package_description": first.description,
        "file": first.file.path,
        "file_definitions": _file_names(first),
        "definitions": [
            {
                "id": f"d{index}",
                "doc": entry.tld.full_doc,
                "signature": entry.tld.declaration[:SIGNATURE_CHARS],
            }
            for index, entry in enumerate(batch)
        ],
    }
    return {
        "model": model,
        "provider": {"order": [provider], "allow_fallbacks": False},
        "temperature": 0,
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "entries", "strict": True, "schema": SCHEMA},
        },
    }


async def _ask(
    client: httpx2.AsyncClient, model: str, provider: str, batch: list[Entry]
) -> tuple[list[Enrichment], float]:
    completion = _Completion.model_validate_json(
        await openrouter.post(client, "/chat/completions", request(model, provider, batch))
    )
    answer = _ANSWER.validate_json(completion.choices[0].message.content)
    items = answer.items if isinstance(answer, _Items) else answer
    by_id = {f"d{index}": entry for index, entry in enumerate(batch)}
    records = [
        Enrichment(
            key=by_id[item.id].content_key,
            role=item.role,
            summary=item.summary.strip(),
            queries=tuple(query.strip() for query in item.queries if query.strip()),
            keywords=tuple(keyword.strip() for keyword in item.keywords if keyword.strip()),
            model=model,
            provider=provider,
        )
        for item in items
        if item.id in by_id
    ]
    return records, completion.usage.cost
