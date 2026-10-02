import base64
import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import Path

from starlette.requests import Request

from jend.corpus import Entry
from jend.index import Document
from jend.limits import Budget, RateLimit
from jend.mirror import Package
from jend.search import Hit
from jend.signatures import Definition, Kind
from jend.web import SECURITY_HEADERS, client_key, hit_html, import_line, page, source_url


def _request(peer: str, headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "client": (peer, 1234),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_client_key_trusts_only_the_configured_header_and_groups_ipv6() -> None:
    spoofed = {"X-Forwarded-For": "1.1.1.1", "CF-Connecting-IP": "203.0.113.9"}
    assert client_key(_request("172.18.0.2", spoofed), "cf-connecting-ip") == "203.0.113.9"
    assert client_key(_request("172.18.0.2", spoofed), None) == "172.18.0.2"
    first = client_key(_request("x", {"CF-Connecting-IP": "2001:db8:1:2::1"}), "cf-connecting-ip")
    second = client_key(
        _request("x", {"CF-Connecting-IP": "2001:db8:1:2:ffff::7"}), "cf-connecting-ip"
    )
    assert first == second == "2001:db8:1:2::/64"


def test_rate_limit_forgets_idle_clients() -> None:
    now = [0.0]
    rate = RateLimit(2, lambda: now[0])
    for client in ("a", "b", "c"):
        rate.record(client)
    now[0] = 61.0
    rate.record("d")

    assert set(rate.calls) == {"d"}


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
    name: str | None, path: str, signature: str = "def f() -> U32", hot: float | None = 1.0
) -> Entry:
    package = Package(
        hash="0x" + "a" * 32,
        name=name,
        version=None if name is None else "1.2.0.0",
        description="",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        hot=hot,
        files=(),
    )
    return Entry(package, 0, path, Definition(Kind.DEF, "f", signature, "", 7))


def test_rate_limit_admits_again_when_the_oldest_call_leaves_the_window() -> None:
    now = [0.0]
    rate = RateLimit(2, lambda: now[0])
    rate.record("a")
    now[0] = 10.0
    rate.record("a")

    assert rate.wait("a") == 50.0
    assert rate.wait("b") == 0.0
    now[0] = 60.0
    assert rate.wait("a") == 0.0


def test_budget_accumulates_per_day_and_survives_a_restart(tmp_path: Path) -> None:
    day = [date(2026, 10, 2)]
    budget = Budget(tmp_path / "b.sqlite", 0.001, lambda: day[0])
    budget.charge(0.0006)
    assert not budget.exhausted()
    budget.close()

    reopened = Budget(tmp_path / "b.sqlite", 0.001, lambda: day[0])
    reopened.charge(0.0006)
    assert reopened.exhausted()
    day[0] = date(2026, 10, 3)
    assert not reopened.exhausted()


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
    assert import_line(_entry("Base", "base.bend", hot=None)) is None


def test_base_links_to_the_line_on_github() -> None:
    assert source_url(_entry("Base", "base.bend", hot=None)).endswith("/bend2/base.bend#L7")


def test_html_escapes_signatures_and_queries() -> None:
    entry = _entry("x", "x.bend", signature="def f() -> {a <script>b</script> : T}")
    row = hit_html(Hit(Document("k", (entry,), None), 0.5))
    body = bytes(page("<img src=x>", row).body).decode()

    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "<img src=x>" not in body
