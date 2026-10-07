import base64
import hashlib
import re
from html import unescape
from pathlib import Path
from urllib.robotparser import RobotFileParser

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jend import embed
from jend.index import INDEX, Record, write
from jend.openrouter import KEY_VARIABLE
from jend.search import PAGE, Hit, Result
from jend.signatures import Kind
from jend.web import (
    ANSWER_CHARS,
    DOC_CHARS,
    ROBOTS,
    SECURITY_HEADERS,
    TEXT_RESULTS,
    call_name,
    create_app,
    hit_html,
    import_line,
    page,
    results_text,
    short_doc,
    source_url,
)


@pytest.mark.parametrize(("tag", "directive"), [("style", "style-src"), ("script", "script-src")])
def test_content_security_policy_allows_the_page_style_and_script(tag: str, directive: str) -> None:
    body = bytes(page("", "").body).decode()
    source = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", body, re.DOTALL)
    assert source is not None
    digest = base64.b64encode(hashlib.sha256(source[1].encode()).digest()).decode()
    assert f"{directive} 'sha256-{digest}'" in SECURITY_HEADERS["Content-Security-Policy"]


def test_results_are_outside_cloudflare_email_obfuscation() -> None:
    body = bytes(page("q", "bend-kit-files@0.1.1.0/files.bend").body).decode()
    start, end = body.index("<!--email_off-->"), body.index("<!--/email_off-->")
    assert start < body.index("bend-kit-files@0.1.1.0") < end


def _entry(
    name: str | None,
    path: str,
    signature: str = "def f() -> U32",
    is_base: bool = False,
    definition: str = "f",
    doc: str | None = None,
) -> Record:
    return Record(
        key="k",
        name=definition,
        kind=Kind.DEF,
        signature=signature,
        doc=doc,
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
    assert import_line(_entry("Base", "base.bend", is_base=True)) == "import Base"


def test_call_name_puts_the_import_alias_before_the_definition_name() -> None:
    assert call_name(_entry("bend-kit-zlib", "zlib.bend", definition="inflate.words")) == (
        "Zlib.inflate.words"
    )
    assert call_name(_entry("Base", "base.bend", is_base=True, definition="String.eq")) == (
        "String.eq"
    )


def test_text_results_give_each_result_its_import_line_in_rank_order() -> None:
    signature = "def gunzip(s: String)\n  -> String"
    zlib = _entry("bend-kit-zlib", "zlib.bend", signature, definition="gunzip", doc="One member.")
    base = _entry("Base", "base.bend", "def String.eq() -> Bool", True, "String.eq")
    unzlib = _entry("bend-kit-zlib", "zlib.bend", definition="unzlib")
    hits = (Hit(zlib, 5.861), Hit(base, 4.0), Hit(unzlib, 3.5))

    text = results_text(Result("gzip", 238, 0, hits))

    assert text == (
        "1. Zlib.gunzip (score 5.86)\n"
        "   import bend-kit-zlib@1.2.0.0/zlib.bend as Zlib\n"
        "   def gunzip(s: String)\n"
        "     -> String\n"
        "   doc: One member.\n"
        f"   source: {source_url(zlib)}\n\n"
        "2. String.eq (score 4.00)\n"
        "   import Base\n"
        "   def String.eq() -> Bool\n"
        f"   source: {source_url(base)}\n\n"
        "3. Zlib.unzlib (score 3.50)\n"
        "   import bend-kit-zlib@1.2.0.0/zlib.bend as Zlib\n"
        "   def f() -> U32\n"
        f"   source: {source_url(unzlib)}\n\n"
        "/search.txt?q=gzip&start=3"
    )


def test_text_results_stop_at_the_size_limit_and_link_to_the_rest() -> None:
    long = _entry("p", "p.bend", "def f() -> U32\n" + "x" * (ANSWER_CHARS // 3))
    hits = tuple(Hit(long, 1.0) for _ in range(5))

    text = results_text(Result("q", 40, 10, hits))

    assert re.findall(r"^(\d+)\. ", text, re.MULTILINE) == ["11", "12"]
    assert text.endswith("\n\n/search.txt?q=q&start=12")


def test_text_results_after_the_end_say_so() -> None:
    assert results_text(Result("q", 40, 40, ())) == "No results after 40."


def test_short_doc_cuts_a_long_comment_at_a_sentence_end() -> None:
    sentence = "Word " * 20 + "end. "
    doc = sentence * 10

    short = short_doc(doc)

    assert short_doc("Short.") == "Short."
    assert len(short) <= DOC_CHARS + 2
    assert short.endswith("end. …")
    assert doc.startswith(short.removesuffix(" …"))
    assert short_doc("x" * 600) == "x" * DOC_CHARS + " …"


def test_base_links_to_the_line_on_github() -> None:
    assert source_url(_entry("Base", "base.bend", is_base=True)).endswith("/bend2/base.bend#L7")


def test_html_escapes_signatures_and_queries() -> None:
    entry = _entry("x", "x.bend", signature="def f() -> {a <script>b</script> : T}")
    row = hit_html(Hit(entry, 0.5))
    body = bytes(page("<img src=x>", row).body).decode()

    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "<img src=x>" not in body


def _signatures(html: str) -> list[str]:
    return [unescape(found) for found in re.findall(r'<pre class="sig"><code>(.*?)</code>', html)]


def test_pages_continue_the_ranking_to_its_end_without_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = 2 * PAGE + 5
    records = [_entry("p", f"f{row}.bend", signature=f"def f{row}() -> U32") for row in range(size)]
    vectors = np.random.default_rng(0).standard_normal((size, 8)).astype(np.float32)
    write(tmp_path / INDEX, records, [r.signature for r in records], vectors, vectors, "test")
    cache = embed.query_cache(tmp_path)
    cache.put([embed.query_text("sort a list")], [vectors[0]])
    cache.close()
    monkeypatch.setenv(KEY_VARIABLE, "test")

    with TestClient(create_app(tmp_path)) as client:
        first = client.get("/", params={"q": "Sort  a list"}).text
        pages = [
            client.get("/more", params={"q": "sort a list", "start": start}).text
            for start in range(PAGE, size + PAGE, PAGE)
        ]
        answers = [client.get("/search.txt", params={"q": "sort a list"}).text]
        while found := re.search(r"^(/search\.txt\S+)$", answers[-1], re.MULTILINE):
            answers.append(client.get(found[1]).text)

    shown = _signatures(first) + [signature for html in pages for signature in _signatures(html)]
    assert f'data-query="sort a list" data-total="{size}"' in first
    assert len(_signatures(first)) == PAGE
    assert [len(_signatures(html)) for html in pages] == [PAGE, 5, 0]
    assert sorted(shown) == sorted(record.signature for record in records)
    assert [re.findall(r"^   (def .*)$", text, re.MULTILINE) for text in answers] == [
        shown[start : start + TEXT_RESULTS] for start in range(0, size, TEXT_RESULTS)
    ]


def test_robots_allow_the_llm_routes_and_keep_crawlers_off_the_html_results() -> None:
    robots = RobotFileParser()
    robots.parse(ROBOTS.splitlines())
    assert robots.can_fetch("*", "/search.txt?q=gzip")
    assert robots.can_fetch("*", "/llms.txt")
    assert not robots.can_fetch("*", "/?q=gzip")
    assert not robots.can_fetch("*", "/more?q=gzip&start=20")
