import sys

from jendhub.db.pool import Pool


async def show_status(pool: Pool) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT g.id, g.corpus, g.base_tag, g.indexer_version, g.state,"
            " count(j.release) FILTER (WHERE j.state = 'queued'),"
            " count(j.release) FILTER (WHERE j.state = 'running'),"
            " count(j.release) FILTER (WHERE j.state IN ('done', 'superseded')),"
            " count(j.release) FILTER (WHERE j.state = 'invalid'),"
            " count(j.release) FILTER (WHERE j.state = 'failed'),"
            " (SELECT count(*) FROM definitions d WHERE d.generation = g.id)"
            " FROM generations g LEFT JOIN index_jobs j ON j.generation = g.id"
            " GROUP BY g.id ORDER BY g.id"
        )
        rows = await cursor.fetchall()
    sys.stdout.write(
        "generation  corpus                state     base     queued  running  done  invalid"
        "  failed  definitions\n"
    )
    for id_, corpus, base, version, state, queued, running, done, invalid, failed, size in rows:
        sys.stdout.write(
            f"{id_:>10}  {corpus:<20}  {state:<8}  {base:<7}  {queued:>6}  {running:>7}"
            f"  {done:>4}  {invalid:>7}  {failed:>6}  {size:>11}  (indexer {version})\n"
        )
