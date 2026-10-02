from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx2
from pydantic import BaseModel

from jend import hub

COMMIT_URL = "https://api.github.com/repos/bendlang/bend/commits/main"
SOURCE_URL = "https://raw.githubusercontent.com/bendlang/bend/{sha}/bend2/base.bend"
PATH = "base.bend"
NAME = "Base"
DESCRIPTION = "Bend's standard library. Every program imports it with `import Base`."


class _Committer(BaseModel):
    date: datetime


class _CommitDetail(BaseModel):
    committer: _Committer


class _Commit(BaseModel):
    sha: str
    commit: _CommitDetail


@dataclass(frozen=True)
class Release:
    sha: str
    published: datetime
    source: Path


async def fetch(cache: Path) -> Release:
    async with httpx2.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(COMMIT_URL, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        commit = _Commit.model_validate_json(response.content)
        source = hub.cached_path(cache, commit.sha, PATH)
        if not source.exists():
            download = await client.get(SOURCE_URL.format(sha=commit.sha))
            download.raise_for_status()
            source.parent.mkdir(parents=True, exist_ok=True)
            partial = source.with_name(source.name + ".partial")
            partial.write_bytes(download.content)
            partial.replace(source)
    return Release(commit.sha, commit.commit.committer.date, source)
