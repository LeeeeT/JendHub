import argparse
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI
from prometheus_client import start_http_server

from jendhub.logs import configure_logging
from jendhub.services.site.app import create_app as create_site
from jendhub.services.syncer.app import create_app as create_syncer
from jendhub.services.worker.app import create_app as create_worker

SERVICES: dict[str, Callable[[], FastAPI]] = {
    "site": create_site,
    "syncer": create_syncer,
    "worker": create_worker,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m jendhub.services")
    parser.add_argument("service", choices=sorted(SERVICES))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=9000,
        help="the internal port of the Prometheus metrics",
    )
    arguments = parser.parse_args()
    configure_logging(structured=True)
    start_http_server(arguments.metrics_port, addr=arguments.host)
    uvicorn.run(
        SERVICES[arguments.service](), host=arguments.host, port=arguments.port, log_config=None
    )


main()
