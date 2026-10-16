import hashlib

import numpy as np
import numpy.typing as npt

MODEL = "qwen/qwen3-embedding-8b"
PROVIDER = "nebius"
DIMENSIONS = 4096
CACHE_KEY = f"{PROVIDER}/{MODEL}"

type Vectors = npt.NDArray[np.float32]


def instructed(instruction: str, query: str) -> str:
    return f"Instruct: {instruction}\nQuery:{query}"


def normalized(vectors: Vectors) -> Vectors:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return (vectors / np.where(norms > 0, norms, 1)).astype(np.float32)


def digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def empty() -> Vectors:
    return np.zeros((0, DIMENSIONS), dtype=np.float32)
