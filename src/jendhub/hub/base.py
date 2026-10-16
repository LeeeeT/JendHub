from dataclasses import dataclass

import httpx

from jendhub.errors import JendHubError

LATEST_RELEASE = "https://api.github.com/repos/bendlang/bend/releases/latest"
SOURCE = "https://raw.githubusercontent.com/bendlang/bend/{tag}/bend2/base.bend"


class BaseError(JendHubError):
    pass


@dataclass(frozen=True)
class Base:
    tag: str
    source: bytes


async def latest_tag(client: httpx.AsyncClient) -> str:
    response = await client.get(LATEST_RELEASE)
    response.raise_for_status()
    match response.json():
        case {"tag_name": str() as tag}:
            return tag
        case _:
            raise BaseError("latest Bend release has no tag")


async def fetch_base(client: httpx.AsyncClient, tag: str) -> Base:
    response = await client.get(SOURCE.format(tag=tag))
    response.raise_for_status()
    return Base(tag, response.content)
