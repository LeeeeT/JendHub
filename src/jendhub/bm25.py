import math
from collections import Counter, defaultdict

import numpy as np
import numpy.typing as npt

from jendhub.tokens import tokens

K1 = 1.2
B = 0.75

type Documents = npt.NDArray[np.intp]
type Impacts = npt.NDArray[np.float64]


def inverse_document_frequency(total: int, matching: int) -> float:
    return math.log(1 + (total - matching + 0.5) / (matching + 0.5))


class Bm25:
    def __init__(self, documents: list[tuple[str, str]]) -> None:
        self.ids = [id_ for id_, _ in documents]
        counts = [Counter(tokens(text)) for _, text in documents]
        lengths = np.array([count.total() for count in counts], dtype=np.float64)
        average = float(lengths.mean()) if lengths.any() else 1.0
        norms = K1 * (1 - B + B * lengths / average)
        postings: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
        for document, count in enumerate(counts):
            for term, frequency in count.items():
                postings[term].append((document, frequency))
        self.impacts = {
            term: self.term_impacts(entries, norms, len(documents))
            for term, entries in postings.items()
        }

    @staticmethod
    def term_impacts(
        entries: list[tuple[int, int]], norms: Impacts, total: int
    ) -> tuple[Documents, Impacts]:
        documents = np.array([document for document, _ in entries], dtype=np.intp)
        frequencies = np.array([frequency for _, frequency in entries], dtype=np.float64)
        idf = inverse_document_frequency(total, len(entries))
        return documents, idf * frequencies * (K1 + 1) / (frequencies + norms[documents])

    def search(self, query: str, limit: int) -> list[str]:
        scores = np.zeros(len(self.ids))
        for term in set(tokens(query)):
            if term in self.impacts:
                documents, impacts = self.impacts[term]
                scores[documents] += impacts
        matches = np.flatnonzero(scores)
        order = matches[np.lexsort((matches, -scores[matches]))]
        return [self.ids[document] for document in order[:limit]]
