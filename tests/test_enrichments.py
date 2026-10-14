from pathlib import Path

import pytest

from jendhub import enrich
from jendhub.definitions import BASE, SourceFile
from jendhub.enrich import Enrichment, windows
from jendhub.enrichments import EnrichmentStore, Stored, window_key

SOURCE = "def a(x):\n  x\n\ndef b(x):\n  x\n"
OTHER = "def c(x):\n  x\n"
A = Enrichment("a", "Does a.", ["first a", "second a"], "api")
B = Enrichment("b", "Does b.", ["b"], "helper")


def test_load_returns_stored_enrichments(tmp_path: Path) -> None:
    store = EnrichmentStore(tmp_path / "enrichments.sqlite")
    [window] = windows(SourceFile(BASE, SOURCE))
    store.store(window, [A, B])
    assert store.load([window]) == Stored({"a": A, "b": B}, [], [])


def test_load_lists_windows_without_enrichments(tmp_path: Path) -> None:
    store = EnrichmentStore(tmp_path / "enrichments.sqlite")
    [window] = windows(SourceFile(BASE, SOURCE))
    [other] = windows(SourceFile(BASE, OTHER))
    store.store(window, [A, B])
    assert store.load([window, other]) == Stored({"a": A, "b": B}, [], [other])


def test_load_lists_a_window_with_some_enrichments_missing(tmp_path: Path) -> None:
    store = EnrichmentStore(tmp_path / "enrichments.sqlite")
    [window] = windows(SourceFile(BASE, SOURCE))
    store.store(window, [A])
    assert store.load([window]) == Stored({}, [], [window])


def test_load_lists_rejected_windows_apart_from_missing_windows(tmp_path: Path) -> None:
    store = EnrichmentStore(tmp_path / "enrichments.sqlite")
    [window] = windows(SourceFile(BASE, SOURCE))
    [other] = windows(SourceFile(BASE, OTHER))
    store.reject(window, "the enricher returned invalid JSON")
    assert store.load([window, other]) == Stored({}, [window], [other])


def test_the_window_key_ignores_the_provider_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    [window] = windows(SourceFile(BASE, SOURCE))
    key = window_key(window)
    monkeypatch.setattr(enrich, "PROVIDERS", ("other",))
    assert window_key(window) == key


def test_a_changed_window_needs_new_enrichments(tmp_path: Path) -> None:
    store = EnrichmentStore(tmp_path / "enrichments.sqlite")
    [window] = windows(SourceFile(BASE, SOURCE))
    [changed] = windows(SourceFile(BASE, SOURCE.replace("  x\n\ndef b", "  x\n# note\ndef b")))
    store.store(window, [A, B])
    assert store.load([changed]) == Stored({}, [], [changed])
