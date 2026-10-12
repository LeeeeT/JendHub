import httpx

CONCURRENCY = 8


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"user-agent": "jendhub"},
        timeout=30,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(
            retries=3,
            limits=httpx.Limits(max_connections=CONCURRENCY),
        ),
    )
