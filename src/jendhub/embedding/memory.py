from collections import OrderedDict

import numpy as np

from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import DIMENSIONS, Vectors, empty


class MemoryCachedEmbedder:
    def __init__(self, remote: Embedder, size: int) -> None:
        self.remote = remote
        self.size = size
        self.found: OrderedDict[str, Vectors] = OrderedDict()

    async def embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return empty()
        missing = list(dict.fromkeys(text for text in texts if text not in self.found))
        if missing:
            self.found.update(zip(missing, await self.remote.embed(missing), strict=True))
        vectors = np.stack([self.found[text] for text in texts]).reshape(-1, DIMENSIONS)
        for text in texts:
            self.found.move_to_end(text)
        while len(self.found) > self.size:
            self.found.popitem(last=False)
        return vectors
