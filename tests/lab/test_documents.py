from jendhub.corpus.definitions import BASE, SourceFile, extract
from jendhub.enrich.enricher import Enrichment
from jendhub.lab.documents import enriched_documents, metadata_documents

DEFINITIONS = extract(SourceFile(BASE, "def a(x):\n  x\n\ndef b(x):\n  x\n"))
A = Enrichment("a", "Does a.", ["find a", "get a"], "api")
B = Enrichment("b", "Does b.", ["find b"], "helper")


def test_enriched_documents_add_the_summary_and_the_queries() -> None:
    assert enriched_documents(DEFINITIONS, {"a": A, "b": B})[0] == (
        "a",
        "def a(x):\n  x\n\nDoes a.\nfind a\nget a",
    )


def test_a_definition_without_enrichment_keeps_only_its_code() -> None:
    assert enriched_documents(DEFINITIONS, {"a": A})[1] == ("b", "def b(x):\n  x")
    assert metadata_documents(DEFINITIONS, {"a": A})[1] == ("b", "def b(x):\n  x")
