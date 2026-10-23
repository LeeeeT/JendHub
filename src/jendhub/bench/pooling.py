from jendhub.bench.queries import Query
from jendhub.bench.searchers import Searcher


async def pool(
    searchers: list[Searcher], queries: list[Query], depth: int
) -> list[tuple[Query, str]]:
    texts = [query.text for query in queries]
    rankings = [await searcher.search(texts, depth) for searcher in searchers]
    pairs: list[tuple[Query, str]] = []
    for index, query in enumerate(queries):
        ids = dict.fromkeys(id_ for ranking in rankings for id_ in ranking[index])
        pairs += [(query, id_) for id_ in ids]
    return pairs


def answer_pairs(queries: list[Query]) -> list[tuple[Query, str]]:
    return [(query, answer.id) for query in queries for answer in query.answers]
