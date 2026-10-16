from datetime import datetime
from typing import Literal

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


class ReleaseSummary(BaseModel):
    version: str
    hash: str
    state: Literal["indexed", "invalid", "unindexed"]
    reason: str | None


class DefinitionSummary(BaseModel):
    id: str
    kind: str
    name: str
    module: str
    head: str


class PackagePage(BaseModel):
    name: str
    releases: list[ReleaseSummary]
    definitions: list[DefinitionSummary]
    definitions_total: int
    definitions_offset: int


class JobCounts(BaseModel):
    queued: int
    running: int
    done: int
    superseded: int
    invalid: int
    failed: int


class GenerationSummary(BaseModel):
    id: int
    state: Literal["building", "live", "retired"]
    base_tag: str
    indexer_version: int
    created_at: datetime
    releases: int
    definitions: int
    jobs: JobCounts


class InvalidReleaseSummary(BaseModel):
    name: str | None
    version: str | None
    hash: str
    base_tag: str
    reason: str
    found_at: datetime


class StatusPage(BaseModel):
    generations: list[GenerationSummary]
    invalid_releases: list[InvalidReleaseSummary]
    invalid_releases_total: int
    invalid_releases_offset: int
