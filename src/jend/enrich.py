import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter

from jend import openrouter
from jend.corpus import Entry

MODEL = "deepseek/deepseek-v4-flash"
BATCH = 24
REQUESTS_IN_FLIGHT = 48
SIGNATURE_CHARS = 1500

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

For each definition, write:
- summary: one plain English sentence of at most 25 words about what the \
definition does or states. Use the words of the problem domain. Do not repeat \
the signature. For a small internal helper, tell which step it performs.
- queries: 3 short search queries (3 to 8 words each) that a developer who \
needs this definition would type. Prefer words that are not in its name.

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
                    "summary": {"type": "string"},
                    "queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "summary", "queries"],
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
    summary: str
    queries: tuple[str, ...]
    model: str


class _Item(BaseModel):
    id: str
    summary: str
    queries: list[str]


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
    records = (Enrichment.model_validate_json(line) for line in path.read_text().splitlines())
    return {record.key: record for record in records}


def batches(entries: Iterable[Entry], done: set[str]) -> list[list[Entry]]:
    by_file: dict[tuple[str, str], list[Entry]] = {}
    scheduled = set(done)
    for entry in entries:
        if entry.content_key in scheduled:
            continue
        scheduled.add(entry.content_key)
        by_file.setdefault((entry.package.hash, entry.path), []).append(entry)
    return [
        group[start : start + BATCH]
        for group in by_file.values()
        for start in range(0, len(group), BATCH)
    ]


async def enrich(
    entries: Iterable[Entry], store: Path, budget: float, model: str = MODEL
) -> Report:
    done = set(load(store))
    work = batches(entries, done)
    report = Report()
    gate = asyncio.Semaphore(REQUESTS_IN_FLIGHT)
    store.parent.mkdir(parents=True, exist_ok=True)
    async with openrouter.connect() as client:
        with store.open("a") as sink:

            async def run(batch: list[Entry]) -> None:
                async with gate:
                    if report.cost >= budget:
                        report.skipped_batches += 1
                        return
                    try:
                        records, cost = await _ask(client, model, batch)
                    except (httpx2.HTTPError, ValueError):
                        report.failed_batches += 1
                        return
                report.cost += cost
                report.written += len(records)
                sink.writelines(record.model_dump_json() + "\n" for record in records)
                sink.flush()

            await asyncio.gather(*(run(batch) for batch in work))
    return report


async def _ask(
    client: httpx2.AsyncClient, model: str, batch: list[Entry]
) -> tuple[list[Enrichment], float]:
    first = batch[0]
    prompt = {
        "package": first.package_label,
        "package_description": first.package.description,
        "file": first.path,
        "definitions": [
            {
                "id": f"d{index}",
                "doc": entry.definition.doc,
                "signature": entry.definition.signature[:SIGNATURE_CHARS],
            }
            for index, entry in enumerate(batch)
        ],
    }
    body: dict[str, object] = {
        "model": model,
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
    completion = _Completion.model_validate_json(
        await openrouter.post(client, "/chat/completions", body)
    )
    answer = _ANSWER.validate_json(completion.choices[0].message.content)
    items = answer.items if isinstance(answer, _Items) else answer
    by_id = {f"d{index}": entry for index, entry in enumerate(batch)}
    records = [
        Enrichment(
            key=by_id[item.id].content_key,
            summary=item.summary.strip(),
            queries=tuple(query.strip() for query in item.queries if query.strip()),
            model=model,
        )
        for item in items
        if item.id in by_id
    ]
    return records, completion.usage.cost
