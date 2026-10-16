from jendhub.corpus.definitions import BASE, Origin, SourceFile, extract
from jendhub.hub.api import Release
from jendhub.search.documents import DOCUMENT_LIMIT, origin_document


def test_a_package_document_starts_with_the_package_and_the_module() -> None:
    origin = Origin(Release("bend-kit-url", "0.4.1.0"), "url")
    [definition] = extract(SourceFile(origin, "def encode(u):\n  u\n"))
    assert origin_document(definition) == "bend-kit-url url\ndef encode(u):\n  u"


def test_a_base_document_starts_with_base() -> None:
    [definition] = extract(SourceFile(BASE, "def a(x):\n  x\n"))
    assert origin_document(definition) == "Base\ndef a(x):\n  x"


def test_a_long_document_is_cut_to_the_limit() -> None:
    [definition] = extract(SourceFile(BASE, "def a(x):\n" + "  x\n" * DOCUMENT_LIMIT))
    assert len(origin_document(definition)) == DOCUMENT_LIMIT
