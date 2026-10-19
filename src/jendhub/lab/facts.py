from dataclasses import dataclass

from jendhub.db.pool import Pool
from jendhub.search.signatures import Signature


@dataclass(frozen=True)
class Facts:
    signature: Signature | None
    documented: bool
    helper: bool
    release_users: int
    external: bool


async def load_facts(pool: Pool, generation: int) -> dict[str, Facts]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT d.id, d.parameters, d.result, d.documented, d.helper, d.release_users,"
            " c.users > d.release_users FROM definitions d JOIN user_counts c"
            " ON c.generation = d.generation AND c.target_key = d.key WHERE d.generation = %s",
            (generation,),
        )
        rows = await cursor.fetchall()
    return {
        id_: Facts(
            None if parameters is None else Signature.from_json(parameters, result),
            documented,
            helper,
            release_users,
            external,
        )
        for id_, parameters, result, documented, helper, release_users, external in rows
    }
