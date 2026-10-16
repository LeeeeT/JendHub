import argparse
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from jendhub.logs import configure_logging
from jendhub.services.api.app import create_app as create_api
from jendhub.services.search.app import create_app as create_search
from jendhub.services.syncer.app import create_app as create_syncer
from jendhub.services.worker.app import create_app as create_worker

SERVICES: dict[str, Callable[[], FastAPI]] = {
    "api": create_api,
    "search": create_search,
    "syncer": create_syncer,
    "worker": create_worker,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m jendhub.services")
    parser.add_argument("service", choices=sorted(SERVICES))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    configure_logging(structured=True)
    uvicorn.run(
        SERVICES[arguments.service](), host=arguments.host, port=arguments.port, log_config=None
    )


main()
