import math
import sqlite3
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

WINDOW_SECONDS = 60.0


class RateLimit:
    def __init__(self, per_minute: int, clock: Callable[[], float]) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self.calls: dict[str, deque[float]] = {}
        self.swept = clock()

    def wait(self, client: str) -> float:
        """Seconds until `client` may start one more call; 0 means it may start now."""
        now = self.clock()
        calls = self.calls.get(client)
        if calls is None:
            return 0.0
        while calls and calls[0] <= now - WINDOW_SECONDS:
            calls.popleft()
        if not calls:
            del self.calls[client]
            return 0.0
        if len(calls) < self.per_minute:
            return 0.0
        return calls[0] + WINDOW_SECONDS - now

    def record(self, client: str) -> None:
        now = self.clock()
        if now - self.swept > WINDOW_SECONDS:
            self.calls = {
                key: calls for key, calls in self.calls.items() if calls[-1] > now - WINDOW_SECONDS
            }
            self.swept = now
        self.calls.setdefault(client, deque()).append(now)


class Budget:
    def __init__(self, path: Path, usd_per_day: float, today: Callable[[], date]) -> None:
        self.usd_per_day = usd_per_day
        self.today = today
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("create table if not exists spent (day text primary key, usd real)")

    def spent(self) -> float:
        row = self.connection.execute(
            "select usd from spent where day = ?", (self.today().isoformat(),)
        ).fetchone()
        return 0.0 if row is None else float(row[0])

    def exhausted(self) -> bool:
        return self.spent() >= self.usd_per_day

    def charge(self, usd: float) -> None:
        with self.connection:
            self.connection.execute(
                "insert into spent values (?, ?)"
                " on conflict (day) do update set usd = usd + excluded.usd",
                (self.today().isoformat(), usd),
            )

    def close(self) -> None:
        self.connection.close()


def utc_today() -> date:
    return datetime.now(UTC).date()


def seconds_until_utc_midnight() -> int:
    now = datetime.now(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    return math.ceil((midnight - now).total_seconds())
