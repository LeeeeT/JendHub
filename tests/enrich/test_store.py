import pytest

from jendhub.corpus.definitions import BASE, SourceFile
from jendhub.db.pool import Pool
from jendhub.enrich import enricher
from jendhub.enrich.enricher import Enrichment
from jendhub.enrich.store import EnrichmentStore, Stored, window_key
from jendhub.enrich.windows import windows

SOURCE = "def a(x):\n  x\n\ndef b(x):\n  x\n"
OTHER = "def c(x):\n  x\n"
A = Enrichment("a", "Does a.", ["first a", "second a"], "api")
B = Enrichment("b", "Does b.", ["b"], "helper")


async def test_load_returns_stored_enrichments(pool: Pool) -> None:
    store = EnrichmentStore(pool)
    [window] = windows(SourceFile(BASE, SOURCE))
    await store.store(window, [A, B])
    assert await store.load([window]) == Stored({"a": A, "b": B}, [], [])


async def test_load_lists_windows_without_enrichments(pool: Pool) -> None:
    store = EnrichmentStore(pool)
    [window] = windows(SourceFile(BASE, SOURCE))
    [other] = windows(SourceFile(BASE, OTHER))
    await store.store(window, [A, B])
    assert await store.load([window, other]) == Stored({"a": A, "b": B}, [], [other])


async def test_load_lists_a_window_with_some_enrichments_missing(pool: Pool) -> None:
    store = EnrichmentStore(pool)
    [window] = windows(SourceFile(BASE, SOURCE))
    await store.store(window, [A])
    assert await store.load([window]) == Stored({}, [], [window])


async def test_load_lists_rejected_windows_apart_from_missing_windows(pool: Pool) -> None:
    store = EnrichmentStore(pool)
    [window] = windows(SourceFile(BASE, SOURCE))
    [other] = windows(SourceFile(BASE, OTHER))
    await store.reject(window, "the enricher returned invalid JSON")
    assert await store.load([window, other]) == Stored({}, [window], [other])


def test_the_window_key_ignores_the_provider_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    [window] = windows(SourceFile(BASE, SOURCE))
    key = window_key(window)
    monkeypatch.setattr(enricher, "PROVIDERS", ("other",))
    assert window_key(window) == key


async def test_a_changed_window_needs_new_enrichments(pool: Pool) -> None:
    store = EnrichmentStore(pool)
    [window] = windows(SourceFile(BASE, SOURCE))
    [changed] = windows(SourceFile(BASE, SOURCE.replace("  x\n\ndef b", "  x\n# note\ndef b")))
    await store.store(window, [A, B])
    assert await store.load([changed]) == Stored({}, [], [changed])
