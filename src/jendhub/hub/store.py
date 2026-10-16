from dataclasses import dataclass

from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, Rejection, Release
from jendhub.hub.base import Base


@dataclass(frozen=True)
class StoredPackage:
    sequence: int
    package: Package


class PackageStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def known_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash FROM packages UNION ALL SELECT hash FROM rejected_packages"
            )
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def stored_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT hash FROM packages")
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def rejected_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT hash FROM rejected_packages")
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def package(self, hash_: str) -> Package:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT p.name, p.version, f.path, f.content FROM packages p"
                " LEFT JOIN package_files f USING (hash) WHERE p.hash = %s",
                (hash_,),
            )
            rows = await cursor.fetchall()
        if not rows:
            raise KeyError(hash_)
        name, version = rows[0][0], rows[0][1]
        release = None if name is None else Release(name, version)
        files = {path: content for _, _, path, content in rows if path is not None}
        return Package(Listing(hash_, release), files)

    async def latest_base_tag(self) -> str | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT tag FROM base_releases ORDER BY stored_at DESC, tag DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return None if row is None else row[0]

    async def base(self, tag: str) -> Base:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT source FROM base_releases WHERE tag = %s", (tag,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(tag)
        return Base(tag, row[0])

    async def store_base(self, base: Base) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO base_releases (tag, source) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (base.tag, base.source),
            )

    async def store_package(self, package: Package) -> None:
        release = package.listing.release
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO packages (hash, name, version) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    package.listing.hash,
                    None if release is None else release.name,
                    None if release is None else release.version,
                ),
            )
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO package_files (hash, path, content) VALUES (%s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    [
                        (package.listing.hash, path, content)
                        for path, content in package.files.items()
                    ],
                )

    async def reject(self, rejection: Rejection) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO rejected_packages (hash, reason) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (rejection.hash, rejection.reason),
            )

    async def packages_after(self, sequence: int, limit: int) -> list[StoredPackage]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT p.sequence, p.hash, p.name, p.version,"
                " coalesce(array_agg(f.path) FILTER (WHERE f.path IS NOT NULL), '{}'),"
                " coalesce(array_agg(f.content) FILTER (WHERE f.path IS NOT NULL), '{}')"
                " FROM packages p LEFT JOIN package_files f USING (hash)"
                " WHERE p.sequence > %s GROUP BY p.sequence, p.hash ORDER BY p.sequence LIMIT %s",
                (sequence, limit),
            )
            rows = await cursor.fetchall()
        return [
            StoredPackage(
                number,
                Package(
                    Listing(hash_, None if name is None else Release(name, version)),
                    dict(zip(paths, contents, strict=True)),
                ),
            )
            for number, hash_, name, version, paths, contents in rows
        ]

    async def releases(self, hashes: list[str]) -> dict[str, Release | None]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash, name, version FROM packages WHERE hash = ANY(%s)", (hashes,)
            )
            rows = await cursor.fetchall()
        return {
            hash_: None if name is None else Release(name, version) for hash_, name, version in rows
        }
