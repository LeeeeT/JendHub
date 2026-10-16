import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jendhub.bench.judgments import JUDGED_GRADES, Judgment
from jendhub.bench.queries import Query
from jendhub.corpus.definitions import Definition
from jendhub.errors import JendHubError
from jendhub.json import Json
from jendhub.remote.openrouter import (
    OpenRouter,
    Usage,
    for_each,
    message_content,
)
from jendhub.uses.graph import UseGraph, reached

MODEL = "openai/gpt-6-luna"
PROVIDER = "openai"
REASONING_EFFORT = "medium"
SEED = 0
CONCURRENCY = 32
PROGRESS_STEP = 500
NEIGHBOR_LIMIT = 10
ROOT_LIMIT = 5
USER_TEMPLATE = "Query: {query}\n\nDefinition ID: {id}\n\n{text}\n\n{uses}\n\n{users}\n\n{roots}"
SECTION_TEMPLATE = "{title} ({count}):"
NEIGHBOR_TEMPLATE = "ID: {id}\n{head}"
MORE_TEMPLATE = "And {count} more."
GRADE_FORMAT: dict[str, Json] = {
    "type": "json_schema",
    "json_schema": {
        "name": "grade",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"grade": {"type": "integer", "enum": list[Json](JUDGED_GRADES)}},
            "required": ["grade"],
            "additionalProperties": False,
        },
    },
}

logger = logging.getLogger(__name__)


class InvalidGradeError(JendHubError):
    pass


@dataclass(frozen=True)
class Pair:
    query: Query
    definition: Definition
    users: tuple[Definition, ...]
    uses: tuple[Definition, ...]
    roots: tuple[Definition, ...]


def roots(id_: str, users: dict[str, frozenset[str]]) -> list[str]:
    return sorted(user for user in reached(id_, users) if user not in users)


def pair(
    query: Query, definition: Definition, graph: UseGraph, definitions: dict[str, Definition]
) -> Pair:
    def neighbors(found: dict[str, frozenset[str]]) -> tuple[Definition, ...]:
        return tuple(definitions[id_] for id_ in sorted(found.get(definition.id, frozenset())))

    reached = tuple(definitions[id_] for id_ in roots(definition.id, graph.users))
    return Pair(query, definition, neighbors(graph.users), neighbors(graph.used), reached)


def section(title: str, neighbors: tuple[Definition, ...], limit: int) -> str:
    shown = [
        NEIGHBOR_TEMPLATE.format(id=neighbor.id, head=neighbor.head)
        for neighbor in neighbors[:limit]
    ]
    hidden = len(neighbors) - len(shown)
    more = [MORE_TEMPLATE.format(count=hidden)] if hidden else []
    return "\n\n".join([SECTION_TEMPLATE.format(title=title, count=len(neighbors)), *shown, *more])


def user_message(pair: Pair) -> str:
    return USER_TEMPLATE.format(
        query=pair.query.text,
        id=pair.definition.id,
        text=pair.definition.text,
        uses=section("Uses", pair.uses, NEIGHBOR_LIMIT),
        users=section("Used by", pair.users, NEIGHBOR_LIMIT),
        roots=section("Reached from", pair.roots, ROOT_LIMIT),
    )


def parse_grade(body: Json) -> int:
    content = message_content(body)
    try:
        answer: Json = json.loads(content)
    except json.JSONDecodeError as error:
        raise InvalidGradeError(f"the judge returned invalid JSON: {content!r}") from error
    match answer:
        case {"grade": int() as grade} if grade in JUDGED_GRADES:
            return grade
        case _:
            raise InvalidGradeError(f"the judge returned an invalid grade: {content!r}")


class Judge:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    def message_length(self, pair: Pair) -> int:
        return len(self.prompt) + len(user_message(pair))

    def request(self, pair: Pair) -> dict[str, Json]:
        return {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": user_message(pair)},
            ],
            "reasoning": {"effort": REASONING_EFFORT},
            "response_format": GRADE_FORMAT,
            "seed": SEED,
            "provider": {"only": [PROVIDER], "allow_fallbacks": False},
        }

    def key(self, pair: Pair) -> bytes:
        return hashlib.sha256(json.dumps(self.request(pair), sort_keys=True).encode()).digest()

    async def judge(
        self,
        openrouter: OpenRouter,
        pairs: list[Pair],
        record: Callable[[Pair, int], Awaitable[None]],
    ) -> Usage:
        usage = Usage()
        if not pairs:
            return usage
        logger.info("judging %d pairs with %s", len(pairs), MODEL)

        async def grade(pair: Pair) -> None:
            body = await openrouter.post("chat/completions", self.request(pair))
            await record(pair, parse_grade(body))
            usage.add(body)
            if usage.requests % PROGRESS_STEP == 0:
                logger.info("judged %d of %d pairs", usage.requests, len(pairs))

        await for_each(pairs, CONCURRENCY, grade)
        logger.info("judged %d pairs: %s", len(pairs), usage.describe())
        return usage


def current_judgments(
    stored: dict[bytes, Judgment],
    queries: list[Query],
    definitions: list[Definition],
    judge: Judge,
    graph: UseGraph,
) -> tuple[list[Judgment], dict[tuple[str, str], Pair]]:
    by_query = {query.text: query for query in queries}
    by_id = {definition.id: definition for definition in definitions}
    judged = {(j.query, j.id) for j in stored.values() if j.query in by_query and j.id in by_id}
    current: list[Judgment] = []
    stale: dict[tuple[str, str], Pair] = {}
    for query, id_ in sorted(judged):
        request = pair(by_query[query], by_id[id_], graph, by_id)
        found = stored.get(judge.key(request))
        if found is None:
            stale[query, id_] = request
        else:
            current.append(found)
    return current, stale
