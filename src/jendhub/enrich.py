import asyncio
import itertools
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files

import httpx

from jendhub.definitions import Definition, Origin, SourceFile, extract
from jendhub.hub import Json
from jendhub.openrouter import OpenRouterError, Usage, for_each, message_content, post

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDERS = ("deepinfra/fp8", "parasail/fp8", "digitalocean")
BEFORE = 40
TARGETS = 40
AFTER = 20
CONTEXT_LIMIT = 30_000
CONCURRENCY = 16
ATTEMPTS = 3
PROGRESS_STEP = 20
ROLES = ("api", "helper")
PROMPT = files("jendhub").joinpath("enrich.md").read_text(encoding="utf-8")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Window:
    origin: Origin
    first: int
    lines: list[str]
    targets: list[Definition]

    def user_message(self) -> str:
        last = self.first + len(self.lines)
        names = "\n".join(target.name for target in self.targets)
        return (
            f"===== {self.origin.label}, lines {self.first + 1}-{last} =====\n"
            + "\n".join(self.lines)
            + f"\n\nWrite the metadata for these definitions:\n{names}"
        )


@dataclass(frozen=True)
class Enrichment:
    id: str
    summary: str
    queries: list[str]
    role: str


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


def response_format(window: Window) -> dict[str, Json]:
    names: list[Json] = [target.name for target in window.targets]
    entry: dict[str, Json] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": names},
            "summary": {"type": "string"},
            "queries": {"type": "array", "items": {"type": "string"}},
            "role": {"type": "string", "enum": list[Json](ROLES)},
        },
        "required": ["name", "summary", "queries", "role"],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "metadata",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"definitions": {"type": "array", "items": entry}},
                "required": ["definitions"],
                "additionalProperties": False,
            },
        },
    }


def parse_entry(item: Json, ids: dict[str, str]) -> tuple[str, Enrichment]:
    match item:
        case {
            "name": str() as name,
            "summary": str() as summary,
            "queries": list() as queries,
            "role": str() as role,
        } if (
            name in ids
            and summary.strip()
            and role in ROLES
            and queries
            and all(isinstance(query, str) and query.strip() for query in queries)
        ):
            texts = [query.strip() for query in queries if isinstance(query, str)]
            return name, Enrichment(ids[name], summary.strip(), texts, role)
        case _:
            raise OpenRouterError(f"the enricher returned an invalid entry: {item!r}")


def parse_enrichments(body: Json, window: Window) -> list[Enrichment]:
    content = message_content(body)
    try:
        answer: Json = json.loads(content)
    except json.JSONDecodeError as error:
        raise OpenRouterError(f"the enricher returned invalid JSON: {error}") from error
    match answer:
        case {"definitions": list() as items}:
            pass
        case _:
            raise OpenRouterError("the enricher returned no definitions")
    ids = {target.name: target.id for target in window.targets}
    entries = dict(parse_entry(item, ids) for item in items)
    if len(entries) != len(items) or entries.keys() != ids.keys():
        missing = sorted(ids.keys() - entries.keys())
        raise OpenRouterError(
            f"the enricher did not return each name once in {window.origin.label}: "
            f"{len(items)} entries, missing {missing}"
        )
    return [entries[target.name] for target in window.targets]


def prompt(window: Window) -> dict[str, Json]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": window.user_message()},
        ],
        "reasoning": {"enabled": False},
        "response_format": response_format(window),
    }


def request(window: Window) -> dict[str, Json]:
    providers = list[Json](PROVIDERS)
    routing: dict[str, Json] = {
        "order": providers,
        "only": providers,
        "allow_fallbacks": True,
        "require_parameters": True,
    }
    body = prompt(window)
    body["provider"] = routing
    return body


@dataclass
class Outcome:
    usage: Usage = field(default_factory=Usage)
    failures: list[str] = field(default_factory=list[str])


def enrich(windows: list[Window], record: Callable[[Window, list[Enrichment]], None]) -> Outcome:
    outcome = Outcome()
    if not windows:
        return outcome
    logger.info("enriching %d windows with %s", len(windows), MODEL)

    async def attempt(client: httpx.AsyncClient, key: str, window: Window) -> list[Enrichment]:
        body = await post(client, key, "chat/completions", request(window))
        outcome.usage.add(body)
        return parse_enrichments(body, window)

    async def enrich_window(client: httpx.AsyncClient, key: str, window: Window) -> None:
        place = f"{window.origin.label} line {window.first + 1}"
        for number in range(1, ATTEMPTS + 1):
            try:
                found = await attempt(client, key, window)
            except (OpenRouterError, httpx.HTTPError) as error:
                logger.info("attempt %d of %d failed for %s: %s", number, ATTEMPTS, place, error)
                if number == ATTEMPTS:
                    outcome.failures.append(f"{place}: {error}")
            else:
                record(window, found)
                done = outcome.usage.requests
                if done % PROGRESS_STEP == 0:
                    logger.info("sent %d requests", done)
                return

    asyncio.run(for_each(windows, CONCURRENCY, enrich_window))
    logger.info("enriched %d windows: %s", len(windows), outcome.usage.describe())
    return outcome
