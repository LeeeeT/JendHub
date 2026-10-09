import base64
import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from urllib.robotparser import RobotFileParser

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jend import embed, sources
from jend.index import INDEX, Record, write
from jend.loader import Library
from jend.mirror import Base, Mirror, Named, Package, extract
from jend.openrouter import KEY_VARIABLE
from jend.parser import Tag
from jend.search import PAGE, Hit, Result
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

ORIGIN = "https://jend.test"


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
        kind=Tag.DEF,
        signature=signature,
        doc=doc,
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

    text = results_text(Result("gzip", 238, 0, hits), ORIGIN)

    assert text == (
        "1. Zlib.gunzip (score 5.86)\n"
        "   import bend-kit-zlib@1.2.0.0/zlib.bend as Zlib\n"
        "   def gunzip(s: String)\n"
        "     -> String\n"
        "   doc: One member.\n"
        f"   source: {ORIGIN}{source_url(zlib)}\n\n"
        "2. String.eq (score 4.00)\n"
        "   import Base\n"
        "   def String.eq() -> Bool\n"
        f"   source: {ORIGIN}{source_url(base)}\n\n"
        "3. Zlib.unzlib (score 3.50)\n"
        "   import bend-kit-zlib@1.2.0.0/zlib.bend as Zlib\n"
        "   def f() -> U32\n"
        f"   source: {ORIGIN}{source_url(unzlib)}\n\n"
        f"More results: {ORIGIN}/search.txt?q=gzip&start=3"
    )


def test_text_results_stop_at_the_size_limit_and_link_to_the_rest() -> None:
    long = _entry("p", "p.bend", "def f() -> U32\n" + "x" * (ANSWER_CHARS // 3))
    hits = tuple(Hit(long, 1.0) for _ in range(5))

    text = results_text(Result("q", 40, 10, hits), ORIGIN)

    assert re.findall(r"^(\d+)\. ", text, re.MULTILINE) == ["11", "12"]
    assert text.endswith(f"\n\nMore results: {ORIGIN}/search.txt?q=q&start=12")


def test_text_results_after_the_end_say_so() -> None:
    assert results_text(Result("q", 40, 40, ()), ORIGIN) == "No results after 40."


def test_a_summary_that_repeats_the_doc_comment_is_left_out() -> None:
    entry = replace(_entry("p", "p.bend", doc="Adds one."), summary="adds  one.")
    assert "summary:" not in results_text(Result("q", 1, 0, (Hit(entry, 1.0),)), ORIGIN)


def test_short_doc_cuts_a_long_comment_at_a_sentence_end() -> None:
    sentence = "Word " * 20 + "end. "
    doc = sentence * 10

    short = short_doc(doc)

    assert short_doc("Short.") == "Short."
    assert len(short) <= DOC_CHARS + 2
    assert short.endswith("end. …")
    assert doc.startswith(short.removesuffix(" …"))
    assert short_doc("x" * 600) == "x" * DOC_CHARS + " …"


def test_source_links_to_the_definition_on_jendhub() -> None:
    assert source_url(_entry("bend-kit-zlib", "src/zlib.bend", definition="inflate.words")) == (
        "/src/bend-kit-zlib@1.2.0.0/src/zlib.bend?def=inflate.words"
    )
    assert source_url(_entry(None, "a b.bend")) == f"/src/0x{'a' * 32}/a%20b.bend?def=f"


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
    records = [
        _entry("p", f"f{row}.bend", f"def f{row}() -> U32", definition=f"f{row}")
        for row in range(size)
    ]
    vectors = np.random.default_rng(0).standard_normal((size, 8)).astype(np.float32)
    write(tmp_path / INDEX, records, [r.signature for r in records], vectors, vectors, "test")
    _write_sources(
        tmp_path, {f"f{row}.bend": f"def f{row}() -> U32:\n  {row}\n" for row in range(size)}
    )
    cache = embed.query_cache(tmp_path)
    cache.put([embed.query_text("sort a list")], [vectors[0]])
    cache.close()
    monkeypatch.setenv(KEY_VARIABLE, "test")

    with TestClient(create_app("http://testserver", tmp_path)) as client:
        first = client.get("/", params={"q": "Sort  a list"}).text
        pages = [
            client.get("/more", params={"q": "sort a list", "start": start}).text
            for start in range(PAGE, size + PAGE, PAGE)
        ]
        answers = [client.get("/search.txt", params={"q": "sort a list"}).text]
        while found := re.search(r"^More results: (\S+)$", answers[-1], re.MULTILINE):
            answers.append(client.get(found[1]).text)
        link = re.search(r"^   source: (\S+)$", answers[0], re.MULTILINE)
        assert link is not None
        definition = client.get(link[1]).text
        refused = client.get("/search.txt", params={"q": "x", "start": -1})

    shown = _signatures(first) + [signature for html in pages for signature in _signatures(html)]
    assert f'data-query="sort a list" data-total="{size}"' in first
    assert len(_signatures(first)) == PAGE
    assert [len(_signatures(html)) for html in pages] == [PAGE, 5, 0]
    assert sorted(shown) == sorted(record.signature for record in records)
    assert [re.findall(r"^   (def .*)$", text, re.MULTILINE) for text in answers] == [
        shown[start : start + TEXT_RESULTS] for start in range(0, size, TEXT_RESULTS)
    ]
    assert definition.splitlines()[0] == shown[0] + ":"
    assert (refused.status_code, refused.text) == (
        400,
        "Invalid parameter start: Input should be greater than or equal to 0.",
    )


def _write_sources(data: Path, files: dict[str, str]) -> None:
    published = datetime(2026, 1, 1, tzinfo=UTC)
    base_key = ("b" * 40, "base.bend")
    texts = {base_key: "type U32 is Data:\n  U32{}\n"}
    texts.update({("0x" + "a" * 32, path): text for path, text in files.items()})
    extracted = extract(Library(texts, {}, base_key), list(texts), base_key[0])
    package = Package(
        hash="0x" + "a" * 32,
        label=Named(name="p", version="1.2.0.0"),
        description="P.",
        published=published,
        hot=1.0,
        files=tuple(file for key, file in extracted.items() if key != base_key),
    )
    base = Base(commit=base_key[0], file=extracted[base_key])
    sources.write(data / INDEX, Mirror(base=base, packages=(package,)))


def test_robots_allow_the_llm_routes_and_keep_crawlers_off_the_html_results() -> None:
    robots = RobotFileParser()
    robots.parse(ROBOTS.splitlines())
    assert robots.can_fetch("*", "/search.txt?q=gzip")
    assert robots.can_fetch("*", "/llms.txt")
    assert robots.can_fetch("*", "/src/p@1.0.0.0/p.bend?def=f")
    assert not robots.can_fetch("*", "/?q=gzip")
    assert not robots.can_fetch("*", "/more?q=gzip&start=20")
