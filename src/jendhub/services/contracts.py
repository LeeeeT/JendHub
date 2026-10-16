from pydantic import BaseModel


class SearchHit(BaseModel):
    id: str
    kind: str
    name: str
    package: str | None
    version: str | None
    module: str
    head: str
    score: float


class SearchResults(BaseModel):
    results: list[SearchHit]


class DefinitionPage(BaseModel):
    id: str
    kind: str
    name: str
    package: str | None
    version: str | None
    module: str
    text: str
    first_line: int
    end_line: int
    uses: list[str]
    used_by: list[str]
    used_by_total: int
    used_by_offset: int
