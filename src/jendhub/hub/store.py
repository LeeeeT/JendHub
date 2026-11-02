import asyncio

from jendhub.db.pool import Pool
from jendhub.hub.api import HubError, Listing, Package, PackageRelease, Rejection, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library
from jendhub.settings import LibrarySettings, load_settings


class PackageStore:
    def __init__(self, pool: Pool, library: Library) -> None:
        self.pool = pool
        self.library = library

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
        labels = await self.releases([hash_])
        if hash_ not in labels:
            raise KeyError(hash_)
        files = await asyncio.to_thread(self.library.read_package, hash_)
        return Package(Listing(hash_, labels[hash_]), files)

    async def latest_base_tag(self) -> str | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT tag FROM base_releases ORDER BY stored_at DESC, tag DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return None if row is None else row[0]

    async def store_base(self, base: Base) -> None:
        await asyncio.to_thread(self.library.store_base, base)
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO base_releases (tag) VALUES (%s) ON CONFLICT DO NOTHING", (base.tag,)
            )

    async def holder(self, release: Release) -> str | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash FROM releases WHERE name = %s AND version = %s",
                (release.name, release.version),
            )
            row = await cursor.fetchone()
        return None if row is None else row[0]

    async def store_package(self, package: Package) -> None:
        await asyncio.to_thread(self.library.store_package, package)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO packages (hash) VALUES (%s) ON CONFLICT DO NOTHING RETURNING hash",
                (package.listing.hash,),
            )
            if await cursor.fetchone() is not None:
                await connection.execute("UPDATE library_state SET revision = revision + 1")
        if package.listing.release is not None:
            await self.store_release(PackageRelease(package.listing.hash, package.listing.release))

    async def store_release(self, package: PackageRelease) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute("SELECT revision FROM library_state FOR UPDATE")
            cursor = await connection.execute(
                "SELECT hash FROM releases WHERE name = %s AND version = %s",
                (package.release.name, package.release.version),
            )
            held = await cursor.fetchone()
            if held is not None and held[0] != package.hash:
                raise HubError(f"the release {package.release} is {held[0]}, not {package.hash}")
            repaired = await asyncio.to_thread(self.library.store_release, package)
            if held is not None:
                if repaired:
                    await connection.execute("UPDATE library_state SET revision = revision + 1")
                return repaired
            await connection.execute(
                "INSERT INTO releases (hash, name, version) VALUES (%s, %s, %s)",
                (package.hash, package.release.name, package.release.version),
            )
            await connection.execute("UPDATE library_state SET revision = revision + 1")
        return True

    async def names(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT DISTINCT name FROM releases")
            return {name for (name,) in await cursor.fetchall()}

    async def revision(self) -> int:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT revision FROM library_state")
            row = await cursor.fetchone()
        if row is None:
            raise HubError("the library has no revision")
        return row[0]

    async def release(self, id_: str) -> PackageRelease:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash, name, version FROM releases WHERE id = %s", (id_,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(id_)
        return PackageRelease(row[0], Release(row[1], row[2]))

    async def reject(self, rejection: Rejection) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO rejected_packages (hash, reason) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (rejection.hash, rejection.reason),
            )

    async def releases(self, hashes: list[str]) -> dict[str, Release | None]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT DISTINCT ON (p.hash) p.hash, r.name, r.version FROM packages p"
                " LEFT JOIN releases r ON r.hash = p.hash WHERE p.hash = ANY(%s)"
                " ORDER BY p.hash, r.name, r.version_order DESC",
                (hashes,),
            )
            rows = await cursor.fetchall()
        return {
            hash_: None if name is None else Release(name, version) for hash_, name, version in rows
        }


def package_store(pool: Pool) -> PackageStore:
    return PackageStore(pool, Library(load_settings(LibrarySettings).library_path))
