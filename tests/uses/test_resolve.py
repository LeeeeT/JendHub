import asyncio
import json
import sys
from io import StringIO
from pathlib import Path
from typing import TextIO

import pytest

from jendhub.uses import resolve as resolver
from jendhub.uses.resolve import UsesError, read_entries, resolve


def entry(path: Path) -> str:
    return (
        json.dumps(
            {
                "path": str(path),
                "error": None,
                "declarations": [],
                "uses": [],
                "highlights": [],
                "targets": [],
            }
        )
        + "\n"
    )


def test_entries_are_decoded_one_at_a_time() -> None:
    first, second = Path("/first.bend"), Path("/second.bend")
    output = StringIO(entry(first) + "not JSON\n")
    found = read_entries(output, [first, second])
    assert next(found).path == first
    with pytest.raises(UsesError, match="invalid JSON"):
        next(found)


@pytest.mark.parametrize("output", ["", "{}\n", "null\n"])
def test_an_incomplete_entry_is_rejected(output: str) -> None:
    with pytest.raises(UsesError):
        list(read_entries(StringIO(output), [Path("/main.bend")]))


def test_an_entry_for_another_file_is_rejected() -> None:
    with pytest.raises(UsesError, match="unexpected entry"):
        list(read_entries(StringIO(entry(Path("/other.bend"))), [Path("/main.bend")]))


def test_extra_entries_are_rejected() -> None:
    path = Path("/main.bend")
    with pytest.raises(UsesError, match="too many entries"):
        list(read_entries(StringIO(entry(path) * 2), [path]))


def test_empty_requests_have_no_entries() -> None:
    assert list(read_entries(StringIO(""), [])) == []


def indexer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    script = tmp_path / "indexer"
    script.write_text(f"#!{sys.executable}\n{source}")
    script.chmod(0o700)
    monkeypatch.setattr(resolver, "INDEXER", str(script))


async def test_a_failed_indexer_reports_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    indexer(tmp_path, monkeypatch, "import sys\nsys.stderr.write('load failed')\nsys.exit(1)")
    with pytest.raises(UsesError, match="failed: load failed"):
        async with resolve(tmp_path, tmp_path / "base.bend", []) as found:
            list(found)


async def test_an_indexer_can_write_more_than_a_pipe_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "main.bend"
    source = (
        f"import sys\nsys.stdin.read()\nsys.stderr.write('x' * 100000)\n"
        f"sys.stdout.write({entry(path)!r})\n"
    )
    indexer(tmp_path, monkeypatch, source)
    async with asyncio.timeout(5), resolve(tmp_path, tmp_path / "base.bend", [path]) as found:
        assert [file.path for file in found] == [path]


async def test_cancellation_stops_the_indexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    indexer(tmp_path, monkeypatch, "import signal\nsignal.pause()\n")
    started = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []
    create = asyncio.create_subprocess_exec

    async def start(
        program: str, *, stdin: int, stdout: TextIO, stderr: TextIO
    ) -> asyncio.subprocess.Process:
        process = await create(program, stdin=stdin, stdout=stdout, stderr=stderr)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    async def run() -> None:
        async with resolve(tmp_path, tmp_path / "base.bend", []) as found:
            list(found)

    task = asyncio.create_task(run())
    async with asyncio.timeout(5):
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert processes[0].returncode is not None
