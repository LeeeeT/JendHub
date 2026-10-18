from dataclasses import dataclass

from jendhub.db.pool import Pool
from jendhub.search.signatures import Signature


@dataclass(frozen=True)
class Facts:
    signature: Signature | None
    documented: bool
    helper: bool
    users: int
    external: bool


async def load_facts(pool: Pool, generation: int) -> dict[str, Facts]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id, parameters, result, documented, helper, users, external"
            " FROM definitions WHERE generation = %s",
            (generation,),
        )
        rows = await cursor.fetchall()
    return {
        id_: Facts(
            None if parameters is None else Signature.from_json(parameters, result),
            documented,
            helper,
            users,
            external,
        )
        for id_, parameters, result, documented, helper, users, external in rows
    }
