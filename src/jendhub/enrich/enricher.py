import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.resources import files

import httpx

from jendhub.enrich.windows import Window
from jendhub.errors import JendHubError
from jendhub.json import Json
from jendhub.remote.openrouter import (
    OpenRouter,
    OpenRouterError,
    Usage,
    for_each,
    message_content,
)

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDERS = ("deepinfra/fp8", "parasail/fp8", "digitalocean")
CONCURRENCY = 16
PROGRESS_STEP = 20
ROLES = ("api", "helper")
PROMPT = files("jendhub.enrich").joinpath("prompt.md").read_text(encoding="utf-8")

logger = logging.getLogger(__name__)


class InvalidAnswerError(JendHubError):
    pass


@dataclass(frozen=True)
class Enrichment:
    id: str
    summary: str
    queries: list[str]
    role: str


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
            raise InvalidAnswerError(f"the enricher returned an invalid entry: {item!r}")


def parse_enrichments(content: str, window: Window) -> list[Enrichment]:
    try:
        answer: Json = json.loads(content)
    except json.JSONDecodeError as error:
        raise InvalidAnswerError(f"the enricher returned invalid JSON: {error}") from error
    match answer:
        case {"definitions": list() as items}:
            pass
        case _:
            raise InvalidAnswerError("the enricher returned no definitions")
    ids = {target.name: target.id for target in window.targets}
    entries = dict(parse_entry(item, ids) for item in items)
    if len(entries) != len(items) or entries.keys() != ids.keys():
        missing = sorted(ids.keys() - entries.keys())
        raise InvalidAnswerError(
            f"the enricher did not return each name once in {window.origin.label}: "
            f"{len(items)} entries, missing {missing}"
        )
    return [entries[target.name] for target in window.targets]


def user_message(window: Window) -> str:
    last = window.first + len(window.lines)
    names = "\n".join(target.name for target in window.targets)
    return (
        f"===== {window.origin.label}, lines {window.first + 1}-{last} =====\n"
        + "\n".join(window.lines)
        + f"\n\nWrite the metadata for these definitions:\n{names}"
    )


def prompt(window: Window) -> dict[str, Json]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": user_message(window)},
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
    rejected: list[str] = field(default_factory=list[str])
    errors: list[str] = field(default_factory=list[str])


async def enrich(
    openrouter: OpenRouter,
    windows: list[Window],
    record: Callable[[Window, list[Enrichment]], Awaitable[None]],
    reject: Callable[[Window, str], Awaitable[None]],
) -> Outcome:
    outcome = Outcome()
    if not windows:
        return outcome
    logger.info("enriching %d windows with %s", len(windows), MODEL)

    async def enrich_window(window: Window) -> None:
        place = f"{window.origin.label} line {window.first + 1}"
        try:
            body = await openrouter.post("chat/completions", request(window))
            outcome.usage.add(body)
            content = message_content(body)
        except (OpenRouterError, httpx.HTTPError) as error:
            logger.info("the request failed for %s: %s", place, error)
            outcome.errors.append(f"{place}: {error}")
            return
        try:
            found = parse_enrichments(content, window)
        except InvalidAnswerError as error:
            logger.info("rejected the answer for %s: %s", place, error)
            await reject(window, str(error))
            outcome.rejected.append(f"{place}: {error}")
            return
        await record(window, found)
        done = outcome.usage.requests
        if done % PROGRESS_STEP == 0:
            logger.info("sent %d requests", done)

    await for_each(windows, CONCURRENCY, enrich_window)
    logger.info("enriched %d windows: %s", len(windows), outcome.usage.describe())
    return outcome
