import hashlib

import httpx
import respx

from jendhub.hub import HUB


def manifest(files: dict[str, bytes]) -> bytes:
    lines = [f"{hashlib.sha256(content).hexdigest()} {path}" for path, content in files.items()]
    return ("\n".join(lines) + "\n").encode()


def package_hash(files: dict[str, bytes]) -> str:
    return "0x" + hashlib.sha256(manifest(files)).hexdigest()[:32]


def serve_package(router: respx.MockRouter, files: dict[str, bytes]) -> str:
    hash_ = package_hash(files)
    router.get(f"{HUB}/{hash_}/manifest").mock(httpx.Response(200, content=manifest(files)))
    for path, content in files.items():
        router.get(f"{HUB}/{hash_}/{path}").mock(httpx.Response(200, content=content))
    return hash_


def listing(hash_: str, name: str | None = None, version: str | None = None) -> dict[str, object]:
    return {"hash": hash_, "name": name, "version": version}


def serve_pages(router: respx.MockRouter, pages: list[list[dict[str, object]]]) -> None:
    route = router.get(f"{HUB}/packages.json")
    route.side_effect = [httpx.Response(200, json={"packages": page}) for page in pages]
