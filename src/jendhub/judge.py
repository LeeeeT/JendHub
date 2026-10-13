import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from jendhub.bench import Query
from jendhub.definitions import Definition
from jendhub.hub import Json
from jendhub.judgments import JUDGED_GRADES
from jendhub.openrouter import OpenRouterError, Usage, for_each, message_content, post

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


def parse_grade(body: Json) -> int:
    content = message_content(body)
    try:
        answer: Json = json.loads(content)
    except json.JSONDecodeError as error:
        raise OpenRouterError(f"the judge returned invalid JSON: {content!r}") from error
    match answer:
        case {"grade": int() as grade} if grade in JUDGED_GRADES:
            return grade
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

    def judge(self, pairs: list[Pair], record: Callable[[Pair, int], None]) -> Usage:
        usage = Usage()
        if not pairs:
            return usage
        logger.info("judging %d pairs with %s", len(pairs), self.name)

        async def grade(client: httpx.AsyncClient, key: str, pair: Pair) -> None:
            body = await post(client, key, "chat/completions", self.request(pair))
            record(pair, parse_grade(body))
            usage.add(body)
            if usage.requests % PROGRESS_STEP == 0:
                logger.info("judged %d of %d pairs", usage.requests, len(pairs))

        asyncio.run(for_each(pairs, CONCURRENCY, grade))
        logger.info("judged %d pairs: %s", len(pairs), usage.describe())
        return usage
