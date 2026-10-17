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
    reached: list[str]


async def load_facts(pool: Pool, generation: int) -> dict[str, Facts]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT d.id, d.parameters, d.result, d.documented, d.helper, d.users, d.external,"
            " ARRAY(SELECT r.id FROM definitions r WHERE r.point = ANY(d.reached) ORDER BY r.id)"
            " FROM definitions d WHERE d.generation = %s",
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
            list(reached),
        )
        for id_, parameters, result, documented, helper, users, external, reached in rows
    }
