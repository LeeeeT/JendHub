import numpy as np

from jendhub.definitions import BASE, Origin, SourceFile, extract
from jendhub.dense import Vectors
from jendhub.enrich import Enrichment
from jendhub.hub import Release
from jendhub.pipelines import (
    Resources,
    enriched_documents,
    metadata_documents,
    origin_documents,
)
from jendhub.uses import UseGraph

DEFINITIONS = extract(SourceFile(BASE, "def a(x):\n  x\n\ndef b(x):\n  x\n"))
EMPTY = UseGraph({}, {}, {}, {})
A = Enrichment("a", "Does a.", ["find a", "get a"], "api")
B = Enrichment("b", "Does b.", ["find b"], "helper")


class NoEmbedder:
    def embed(self, texts: list[str]) -> Vectors:
        return np.zeros((len(texts), 1), dtype=np.float32)


def test_enriched_documents_add_the_summary_and_the_queries() -> None:
    resources = Resources(DEFINITIONS, NoEmbedder(), {"a": A, "b": B}, EMPTY)
    assert enriched_documents(resources)[0] == ("a", "def a(x):\n  x\n\nDoes a.\nfind a\nget a")


def test_a_definition_without_enrichment_keeps_only_its_code() -> None:
    resources = Resources(DEFINITIONS, NoEmbedder(), {"a": A}, EMPTY)
    assert enriched_documents(resources)[1] == ("b", "def b(x):\n  x")
    assert metadata_documents(resources)[1] == ("b", "def b(x):\n  x")


def test_origin_documents_start_with_the_package_and_the_module() -> None:
    origin = Origin(Release("bend-kit-url", "0.4.1.0"), "url")
    package = extract(SourceFile(origin, "def encode(u):\n  u\n"))
    assert origin_documents(package + DEFINITIONS[:1]) == [
        ("bend-kit-url@0.4.1.0/url:encode", "bend-kit-url url\ndef encode(u):\n  u"),
        ("a", "Base\ndef a(x):\n  x"),
    ]
