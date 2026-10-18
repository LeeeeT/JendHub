from collections.abc import Iterable, Mapping
from typing import Annotated, Any

from fastapi import Query

MAX_QUERY = 1000
MAX_LIMIT = 50
PAGE = 100

QueryText = Annotated[str, Query(min_length=1, max_length=MAX_QUERY)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]
SearchOffset = Annotated[int, Query(ge=0, lt=MAX_LIMIT)]


def invalid_parameters(problems: Iterable[Mapping[str, Any]]) -> str:
    named = "; ".join(f"{problem['loc'][-1]}: {problem['msg']}" for problem in problems)
    return f"Invalid parameter {named}."
