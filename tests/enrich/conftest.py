import pytest


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jendhub.enrich.windows.BEFORE", 2)
    monkeypatch.setattr("jendhub.enrich.windows.TARGETS", 2)
    monkeypatch.setattr("jendhub.enrich.windows.AFTER", 1)
