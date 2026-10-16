from http import HTTPStatus
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from jendhub.services.contracts import DefinitionPage, SearchResults


def checked(response: httpx.Response) -> httpx.Response:
    if response.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
        raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, "the search index is not ready")
    if response.is_error:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, "the search service failed")
    return response


class SearchClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def search(self, query: str, limit: int) -> SearchResults:
        response = await self.client.get("/search", params={"q": query, "limit": limit})
        return SearchResults.model_validate_json(checked(response).content)

    async def definition(self, id_: str, offset: int, limit: int) -> DefinitionPage | None:
        response = await self.client.get(
            f"/definitions/{quote(id_, safe='/@:')}",
            params={"used_by_offset": offset, "used_by_limit": limit},
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        return DefinitionPage.model_validate_json(checked(response).content)
