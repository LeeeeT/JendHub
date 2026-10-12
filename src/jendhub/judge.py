import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from jendhub.bench import Query
from jendhub.definitions import Definition
from jendhub.http import http_client
from jendhub.hub import Json
from jendhub.judgments import JUDGED_GRADES
from jendhub.openrouter import OpenRouterError, api_key, post

MODEL = "openai/gpt-6-luna"
PROVIDER = "openai"
REASONING_EFFORT = "medium"
SEED = 0
CONCURRENCY = 32
PROGRESS_STEP = 500
USER_TEMPLATE = "Query: {query}\nQuery kind: {kind}\n\nDefinition ID: {id}\n\n{text}"
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


@dataclass(frozen=True)
class Pair:
    query: Query
    definition: Definition


def user_message(pair: Pair) -> str:
    return USER_TEMPLATE.format(
        query=pair.query.query,
        kind=pair.query.kind,
        id=pair.definition.id,
        text=pair.definition.text,
    )


def message_content(body: Json) -> str:
    match body:
        case {"choices": [{"message": {"content": str() as content}}, *_]}:
            return content
        case _:
            raise OpenRouterError("OpenRouter returned no message")


def response_cost(body: Json) -> float:
    match body:
        case {"usage": {"cost": int() | float() as cost}}:
            return float(cost)
        case _:
            return 0.0


def parse_grade(body: Json) -> tuple[int, float]:
    content = message_content(body)
    try:
        answer: Json = json.loads(content)
    except json.JSONDecodeError as error:
        raise OpenRouterError(f"the judge returned invalid JSON: {content!r}") from error
    match answer:
        case {"grade": int() as grade} if grade in JUDGED_GRADES:
            return grade, response_cost(body)
        case _:
            raise OpenRouterError(f"the judge returned an invalid grade: {content!r}")


class Judge:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        digest = hashlib.sha256(f"{prompt}\n{USER_TEMPLATE}".encode()).hexdigest()[:8]
        self.name = f"{MODEL} {REASONING_EFFORT} {digest}"

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

    async def grade(self, client: httpx.AsyncClient, key: str, pair: Pair) -> tuple[int, float]:
        return parse_grade(await post(client, key, "chat/completions", self.request(pair)))

    def judge(self, pairs: list[Pair], record: Callable[[Pair, int], None]) -> float:
        if not pairs:
            return 0.0
        logger.info("judging %d pairs with %s", len(pairs), self.name)
        return asyncio.run(self.judge_all(pairs, record))

    async def judge_all(self, pairs: list[Pair], record: Callable[[Pair, int], None]) -> float:
        key = api_key()
        pending: Iterator[Pair] = iter(pairs)
        costs: list[float] = []

        async def work(client: httpx.AsyncClient) -> None:
            for pair in pending:
                grade, cost = await self.grade(client, key, pair)
                record(pair, grade)
                costs.append(cost)
                if len(costs) % PROGRESS_STEP == 0:
                    logger.info("judged %d of %d pairs", len(costs), len(pairs))

        async with http_client(CONCURRENCY) as client:
            await asyncio.gather(*(work(client) for _ in range(CONCURRENCY)))
        logger.info("judged %d pairs for $%.4f", len(costs), sum(costs))
        return sum(costs)
