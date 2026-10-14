import numpy as np

from jendhub.definitions import BASE, SourceFile, extract
from jendhub.dense import Vectors
from jendhub.enrich import Enrichment
from jendhub.pipelines import Resources, enriched_documents, metadata_documents

DEFINITIONS = extract(SourceFile(BASE, "def a(x):\n  x\n\ndef b(x):\n  x\n"))
A = Enrichment("a", "Does a.", ["find a", "get a"], "api")
B = Enrichment("b", "Does b.", ["find b"], "helper")


class NoEmbedder:
    def embed(self, texts: list[str]) -> Vectors:
        return np.zeros((len(texts), 1), dtype=np.float32)


def test_enriched_documents_add_the_summary_and_the_queries() -> None:
    resources = Resources(DEFINITIONS, NoEmbedder(), {"a": A, "b": B})
    assert enriched_documents(resources)[0] == ("a", "def a(x):\n  x\n\nDoes a.\nfind a\nget a")


def test_a_definition_without_enrichment_keeps_only_its_code() -> None:
    resources = Resources(DEFINITIONS, NoEmbedder(), {"a": A})
    assert enriched_documents(resources)[1] == ("b", "def b(x):\n  x")
    assert metadata_documents(resources)[1] == ("b", "def b(x):\n  x")
