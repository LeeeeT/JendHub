from collections import Counter

from jendhub.definitions import BASE, SourceFile, extract
from jendhub.signatures import (
    Signature,
    SignatureMatch,
    head_signature,
    query_signature,
    similarity,
)

MAP = "def List.map(~A: Type, ~B: Type, ~f: A -> B, xs: List<&2, A>) -> List<&2, B>:"
GET = (
    "# Sends a GET request.\ndef get(c: Client, url: String) -> IO(Client & Result<&1, Http.Res>):"
)


def test_a_head_gives_the_types_of_its_parameters_and_its_result() -> None:
    assert head_signature(MAP) == Signature(
        [Counter({"T": 2}), Counter({"List": 1, "T": 1})], Counter({"List": 1, "T": 1})
    )


def test_a_head_drops_its_doc_comment_and_module_qualifiers() -> None:
    assert head_signature(GET) == Signature(
        [Counter({"Client": 1}), Counter({"String": 1})],
        Counter({"IO": 1, "Client": 1, "Result": 1, "Res": 1}),
    )


def test_a_head_that_spans_lines_is_one_signature() -> None:
    head = "def Heap.to_list(\n  ~A: Data,\n  h: Heap<A>\n) -> List<A>:"
    assert head_signature(head) == Signature(
        [Counter({"Heap": 1, "T": 1})], Counter({"List": 1, "T": 1})
    )


def test_a_query_with_an_arrow_is_a_signature() -> None:
    assert query_signature("(A -> B), List<A> -> List<B>") == head_signature(MAP)


def test_a_query_can_be_a_head() -> None:
    assert query_signature(MAP) == head_signature(MAP)


def test_a_query_without_an_arrow_is_not_a_signature() -> None:
    assert query_signature("map a list") is None


def test_similarity_is_one_for_the_same_types_in_any_parameter_order() -> None:
    wanted = query_signature("List<A>, (A -> B) -> List<B>")
    found = head_signature(MAP)
    assert wanted is not None
    assert found is not None
    assert similarity(wanted, found) == 1.0


def test_similarity_counts_the_shared_types() -> None:
    wanted = query_signature("Client, String -> IO<Client, Result<Response>>")
    found = head_signature(GET)
    assert wanted is not None
    assert found is not None
    assert similarity(wanted, found) == (3 / 5 + 1) / 2


def test_signature_match_scores_only_signature_queries() -> None:
    definitions = extract(SourceFile(BASE, f"{MAP}\n  xs\n\ntype List<A> is Data:\n  Nil{{}}\n"))
    match = SignatureMatch(definitions)
    assert match.scores(
        ["List<A>, (A -> B) -> List<B>", "map a list"], [["List.map", "List"], ["List.map"]]
    ) == [[1.0, 0.0], [0.0]]
