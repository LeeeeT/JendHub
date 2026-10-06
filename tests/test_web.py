import base64
import hashlib
import re

from jend.index import Record
from jend.search import Hit
from jend.signatures import Kind
from jend.web import SECURITY_HEADERS, hit_html, import_line, page, source_url


def test_content_security_policy_allows_the_page_style() -> None:
    body = bytes(page("", "").body).decode()
    style = re.search(r"<style>(.*?)</style>", body, re.DOTALL)
    assert style is not None
    digest = base64.b64encode(hashlib.sha256(style[1].encode()).digest()).decode()
    assert f"'sha256-{digest}'" in SECURITY_HEADERS["Content-Security-Policy"]


def test_results_are_outside_cloudflare_email_obfuscation() -> None:
    body = bytes(page("q", "bend-kit-files@0.1.1.0/files.bend").body).decode()
    start, end = body.index("<!--email_off-->"), body.index("<!--/email_off-->")
    assert start < body.index("bend-kit-files@0.1.1.0") < end


def _entry(
    name: str | None, path: str, signature: str = "def f() -> U32", is_base: bool = False
) -> Record:
    return Record(
        key="k",
        name="f",
        kind=Kind.DEF,
        signature=signature,
        line=7,
        path=path,
        package_hash="0x" + "a" * 32,
        package_name=name,
        package_version=None if name is None else "1.2.0.0",
        package_rank=0,
        is_base=is_base,
        role=None,
        summary=None,
    )


def test_import_line_names_the_package_or_its_hash() -> None:
    assert import_line(_entry("bend-kit-zlib", "zlib.bend")) == (
        "import bend-kit-zlib@1.2.0.0/zlib.bend as Zlib"
    )
    assert import_line(_entry("emerging-ezjson", "main.bend")) == (
        "import emerging-ezjson@1.2.0.0/main.bend as EmergingEzjson"
    )
    assert import_line(_entry(None, "src/containers/bit_set.bend")) == (
        f"import 0x{'a' * 32}/src/containers/bit_set.bend as BitSet"
    )
    assert import_line(_entry("Base", "base.bend", is_base=True)) is None


def test_base_links_to_the_line_on_github() -> None:
    assert source_url(_entry("Base", "base.bend", is_base=True)).endswith("/bend2/base.bend#L7")


def test_html_escapes_signatures_and_queries() -> None:
    entry = _entry("x", "x.bend", signature="def f() -> {a <script>b</script> : T}")
    row = hit_html(Hit(entry, 0.5))
    body = bytes(page("<img src=x>", row).body).decode()

    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "<img src=x>" not in body
