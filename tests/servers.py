import os
import socket
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx

USER = "jendhub"
TEMPLATE = "jendhub_template"
STARTUP_SECONDS = 30


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


@contextmanager
def postgres(root: Path) -> Generator[Path]:
    cluster = root / "cluster"
    sockets = root / "socket"
    sockets.mkdir(parents=True)
    subprocess.run(
        [
            "initdb",
            f"--pgdata={cluster}",
            f"--username={USER}",
            "--auth=trust",
            "--encoding=UTF8",
            "--locale=C",
        ],
        check=True,
        capture_output=True,
    )
    options = f"-k {sockets} -c listen_addresses='' -c fsync=off -c full_page_writes=off"
    subprocess.run(
        [
            "pg_ctl",
            f"--pgdata={cluster}",
            f"--log={root / 'postgres.log'}",
            "--wait",
            f"--options={options}",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield sockets
    finally:
        subprocess.run(
            ["pg_ctl", f"--pgdata={cluster}", "--mode=immediate", "stop"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def database_url(sockets: Path, database: str) -> str:
    return f"postgresql://{USER}@/{database}?host={sockets}"


@contextmanager
def qdrant(root: Path) -> Generator[str]:
    http_port = free_port()
    environment = os.environ | {
        "QDRANT__STORAGE__STORAGE_PATH": str(root / "storage"),
        "QDRANT__STORAGE__SNAPSHOTS_PATH": str(root / "snapshots"),
        "QDRANT__SERVICE__HTTP_PORT": str(http_port),
        "QDRANT__SERVICE__GRPC_PORT": str(free_port()),
        "QDRANT__STORAGE__WAL__WAL_CAPACITY_MB": "1",
        "QDRANT__TELEMETRY_DISABLED": "true",
    }
    process = subprocess.Popen(
        ["qdrant"],
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{http_port}"
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            try:
                if httpx.get(f"{url}/readyz").is_success:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("qdrant did not start")
            time.sleep(0.1)
        yield url
    finally:
        process.terminate()
        process.wait()
