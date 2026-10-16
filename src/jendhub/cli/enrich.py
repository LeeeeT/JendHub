import sys

from jendhub.corpus.corpus import Corpus
from jendhub.enrich.enricher import enrich
from jendhub.enrich.store import EnrichError, EnrichmentStore
from jendhub.enrich.windows import corpus_windows
from jendhub.remote.openrouter import OpenRouter


async def enrich_corpus(corpus: Corpus, store: EnrichmentStore, openrouter: OpenRouter) -> None:
    stored = await store.load(corpus_windows(corpus))
    sys.stdout.write(
        f"{len(stored.enrichments)} definitions have enrichments;"
        f" {len(stored.rejected)} windows are rejected; {len(stored.missing)} windows remain\n"
    )
    outcome = await enrich(openrouter, stored.missing, store.store, store.reject)
    for rejection in outcome.rejected:
        sys.stdout.write(f"rejected: {rejection}\n")
    for error in outcome.errors:
        sys.stdout.write(f"failed: {error}\n")
    if outcome.errors:
        raise EnrichError(f"{len(outcome.errors)} requests failed: run jendhub enrich again")
