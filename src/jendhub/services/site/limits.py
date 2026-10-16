import time
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import Request

MAX_CLIENTS = 100_000


@dataclass
class Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimit:
    per_minute: float
    burst: float
    buckets: OrderedDict[str, Bucket] = field(default_factory=OrderedDict[str, Bucket])

    def take(self, client: str, now: float | None = None) -> float:
        moment = time.monotonic() if now is None else now
        bucket = self.buckets.pop(client, None) or Bucket(self.burst, moment)
        rate = self.per_minute / 60
        bucket.tokens = min(self.burst, bucket.tokens + (moment - bucket.updated) * rate)
        bucket.updated = moment
        self.buckets[client] = bucket
        while len(self.buckets) > MAX_CLIENTS:
            self.buckets.popitem(last=False)
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / rate


def client_address(request: Request, header: str | None) -> str:
    if header is not None and (forwarded := request.headers.get(header)):
        return forwarded
    return "unknown" if request.client is None else request.client.host
