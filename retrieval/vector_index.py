"""Embeddings déterministes (sac de mots haché) + utilitaires cosinus."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)
_STOP = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "en", "au", "aux",
    "que", "qui", "dans", "pour", "par", "sur", "avec", "est", "sont", "ce",
    "cette", "ces", "il", "elle", "ils", "elles", "on", "ne", "pas", "plus",
    "se", "sa", "son", "ses", "leur", "leurs", "a", "the", "of", "and", "to",
}


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [t for t in _WORD.findall(text) if len(t) > 2 and t not in _STOP]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


class DeterministicHashEmbedding:
    """Sac de mots haché, L2-normalisé. Ce n'est PAS un embedding sémantique."""

    name = "DeterministicHashEmbedding"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        for tok in tokens:
            for grain in (tok, tok[:5]):
                h = hashlib.blake2b(grain.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                sign = 1.0 if h[4] % 2 == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]
