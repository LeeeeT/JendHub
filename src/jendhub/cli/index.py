import sys

from jendhub.db.pool import Pool
from jendhub.indexing.status import generation_statuses


async def show_status(pool: Pool) -> None:
    sys.stdout.write(
        "generation  corpus                state     base     queued  running  done  invalid"
        "  failed  definitions\n"
    )
    for status in await generation_statuses(pool, None):
        jobs = status.jobs
        done = jobs.done + jobs.superseded
        sys.stdout.write(
            f"{status.id:>10}  {status.corpus:<20}  {status.state:<8}  {status.base_tag:<7}"
            f"  {jobs.queued:>6}  {jobs.running:>7}  {done:>4}  {jobs.invalid:>7}"
            f"  {jobs.failed:>6}  {status.definitions:>11}  (indexer {status.indexer_version})\n"
        )
