from typing import Annotated

from fastapi import Query

MAX_QUERY = 1000
MAX_LIMIT = 50
MAX_PAGE = 500
PAGE = 100

QueryText = Annotated[str, Query(min_length=1, max_length=MAX_QUERY)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
