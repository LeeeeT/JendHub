from pathlib import Path

from jend.search import RANKER_VERSION, ranking_sources, source_version


def test_source_version_changes_when_a_ranking_source_changes(tmp_path: Path) -> None:
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    first.write_text("CANDIDATES = 50\n")
    second.write_text("RRF_K = 60\n")
    before = source_version([first, second])

    second.write_text("RRF_K = 30\n")

    assert source_version([first, second]) != before


def test_ranker_version_covers_existing_sources_including_this_module() -> None:
    sources = ranking_sources()

    assert all(path.is_file() for path in sources)
    assert any(path.name == "search.py" for path in sources)
    assert RANKER_VERSION == source_version(sources)
