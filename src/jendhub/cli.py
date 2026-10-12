import argparse
import asyncio
import logging
from pathlib import Path

from jendhub.mirror import Mirror
from jendhub.sync import sync


def main() -> None:
    parser = argparse.ArgumentParser(prog="jendhub")
    parser.add_argument("--data", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="mirror BendHub and the latest released base.bend")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    match arguments.command:
        case "sync":
            asyncio.run(sync(Mirror(arguments.data)))
        case _:
            parser.error(f"unknown command {arguments.command}")
