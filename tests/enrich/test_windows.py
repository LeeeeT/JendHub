import pytest

from jendhub.corpus.definitions import BASE, SourceFile
from jendhub.enrich.windows import windows
from tests.enrich.fixture import source


@pytest.mark.usefixtures("small")
def test_windows_split_the_targets_and_add_context_on_both_sides() -> None:
    found = windows(SourceFile(BASE, source(7)))
    assert [[target.name for target in window.targets] for window in found] == [
        ["f0", "f1"],
        ["f2", "f3"],
        ["f4", "f5"],
        ["f6"],
    ]
    middle = found[1]
    assert middle.lines[0] == "# Step 0."
    assert middle.lines[-1] == "  n"
    assert "def f4(n: Nat) -> Nat:" in middle.lines
    assert "def f5(n: Nat) -> Nat:" not in middle.lines


@pytest.mark.usefixtures("small")
def test_windows_drop_context_beyond_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jendhub.enrich.windows.CONTEXT_LIMIT", 40)
    window = windows(SourceFile(BASE, source(7)))[1]
    assert window.lines[0] == "# Step 1."
    assert "def f4(n: Nat) -> Nat:" in window.lines


def test_windows_keep_the_lines_between_definitions() -> None:
    text = "law f:\n  Nat\n\ndef f(n):\n  n\n\n# Section\n\ndef g(n: Nat) -> Nat:\n  f(n)\n"
    [window] = windows(SourceFile(BASE, text))
    assert window.lines == text.rstrip().splitlines()
