import asyncio
import itertools
import tempfile
from collections.abc import AsyncGenerator, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
import respx
from psycopg import sql

from jendhub.db.migrate import migrate
from jendhub.db.pool import Pool, open_pool
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import VectorIndex, open_vector_index
from tests.servers import TEMPLATE, database_url, postgres, qdrant

DATABASES = itertools.count()
MEMORY = Path("/dev/shm")  # noqa: S108


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(scope="session")
def postgres_sockets(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    with postgres(tmp_path_factory.mktemp("postgres")) as sockets:
        with psycopg.connect(database_url(sockets, "postgres"), autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEMPLATE)))
        yield sockets


@pytest.fixture(scope="session")
def migrated_template(postgres_sockets: Path) -> str:
    async def run() -> None:
        url = database_url(postgres_sockets, TEMPLATE)
        async with await psycopg.AsyncConnection.connect(url) as connection:
            await migrate(connection)

    asyncio.run(run())
    return TEMPLATE


@pytest.fixture
def database(postgres_sockets: Path, migrated_template: str) -> DatabaseSettings:
    name = f"test_{next(DATABASES)}"
    with psycopg.connect(database_url(postgres_sockets, "postgres"), autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(migrated_template)
            )
        )
    return DatabaseSettings(database_url=database_url(postgres_sockets, name))


@pytest.fixture
async def pool(database: DatabaseSettings) -> AsyncGenerator[Pool]:
    async with open_pool(database) as opened:
        yield opened


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(tmp_path / "library")


@pytest.fixture
def packages(pool: Pool, library: Library) -> PackageStore:
    return PackageStore(pool, library)


@contextmanager
def memory_directory(tmp_path_factory: pytest.TempPathFactory, name: str) -> Generator[Path]:
    if not MEMORY.is_dir():
        yield tmp_path_factory.mktemp(name)
        return
    with tempfile.TemporaryDirectory(dir=MEMORY, prefix=f"jendhub-{name}-") as root:
        yield Path(root)


@pytest.fixture(scope="session")
def qdrant_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    with memory_directory(tmp_path_factory, "qdrant") as root, qdrant(root) as url:
        yield url


@pytest.fixture
def qdrant_settings(qdrant_url: str) -> QdrantSettings:
    return QdrantSettings(qdrant_url=qdrant_url)


@pytest.fixture
async def vectors(qdrant_settings: QdrantSettings) -> AsyncGenerator[VectorIndex]:
    async with open_vector_index(qdrant_settings) as opened:
        yield opened
