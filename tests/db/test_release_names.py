import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from jendhub.db import migrate as module
from jendhub.settings import DatabaseSettings

HASH = "0x" + "a" * 32


async def test_migration_preserves_existing_index_and_package_names(
    database: DatabaseSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = module.migrations()
    async with await psycopg.AsyncConnection.connect(database.database_url) as connection:
        await connection.execute("DROP SCHEMA public CASCADE")
        await connection.execute("CREATE SCHEMA public")
        monkeypatch.setattr(
            module,
            "migrations",
            lambda: [script for script in scripts if script[0] < "0013_release_names.sql"],
        )
        await module.migrate(connection)
        await connection.execute("INSERT INTO base_releases (tag) VALUES ('v2.0.38')")
        await connection.execute(
            "INSERT INTO packages (hash, name, version) VALUES (%s, 'kit', '1.0.0.0')",
            (HASH,),
        )
        cursor = await connection.execute(
            "INSERT INTO generations (corpus, base_tag, indexer_version, dimensions, quantization)"
            " VALUES ('live', 'v2.0.38', 5, 2048, 'int8') RETURNING id",
        )
        row = await cursor.fetchone()
        assert row is not None
        generation = row[0]
        await connection.execute(
            "INSERT INTO index_jobs (generation, release, name) VALUES (%s, %s, 'kit')",
            (generation, HASH),
        )
        await connection.execute(
            "INSERT INTO highlights (generation, release, module, lines)"
            " VALUES (%s, %s, 'main', '[]')",
            (generation, HASH),
        )
        await connection.execute(
            "INSERT INTO invalid_releases (hash, base_tag, errors) VALUES (%s, 'v2.0.38', %s)",
            (
                HASH,
                Jsonb(
                    {
                        "main.bend": {
                            "expected": "a package named missing@1.0.0.0 on https://hub.bend-lang.com"
                        },
                        "hash.bend": {
                            "expected": "a file at https://hub.bend-lang.com/0x"
                            + "b" * 32
                            + "/manifest hashing to "
                            + "b" * 32
                        },
                        "own.bend": {
                            "expected": "a file at https://hub.bend-lang.com/"
                            + HASH
                            + "/manifest hashing to "
                            + "a" * 32
                        },
                    }
                ),
            ),
        )
        monkeypatch.setattr(module, "migrations", lambda: scripts)
        assert await module.migrate(connection) == [
            "0013_release_names.sql",
            "0014_invalid_dependencies.sql",
        ]
        for table in ["index_jobs", "highlights", "invalid_releases"]:
            cursor = await connection.execute(
                sql.SQL("SELECT release FROM {}").format(sql.Identifier(table)),
            )
            assert await cursor.fetchall() == [("kit@1.0.0.0",)]
        await connection.execute(
            "INSERT INTO releases (name, version, hash) VALUES ('alias', '1.0.0.0', %s)",
            (HASH,),
        )
        cursor = await connection.execute("SELECT name, hash FROM releases ORDER BY name")
        assert await cursor.fetchall() == [("alias", HASH), ("kit", HASH)]
        cursor = await connection.execute("SELECT * FROM packages")
        assert [column.name for column in cursor.description or []] == ["hash", "stored_at"]
        cursor = await connection.execute(
            "SELECT library_revision, indexer_version FROM invalid_releases"
        )
        assert await cursor.fetchall() == [(-1, -1)]
        cursor = await connection.execute(
            "SELECT missing_names, missing_hashes FROM invalid_releases"
        )
        assert await cursor.fetchall() == [(["missing@1.0.0.0"], ["0x" + "b" * 32])]
