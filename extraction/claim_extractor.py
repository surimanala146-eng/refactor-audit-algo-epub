"""Extraction lexicale déterministe des propositions (claims)."""
from __future__ import annotations

import hashlib
import re

from domain.models import Claim
from ingestion.segmenter import Sentence

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)
# Marqueurs d'assertion : on privilégie les phrases qui affirment quelque chose.
_ASSERTIVE = (
    " est ", " sont ", " était ", " étaient ", " a ", " ont ", " doit ", " doivent ",
    " permet ", " montre ", " prouve ", " signifie ", " implique ", " contient ",
    " indique ", " définit ", " repose ", " dépend ", " provient ", " n'est ",
)


class NullExtractionModel:
    """Baseline lexicale, sans ML : sélection de phrases assertives.

    Déterministe (aucun aléa), donc reproductible d'un run à l'autre.
    """

    name = "NullExtractionModel"

    def __init__(
        self,
        min_words: int = 8,
        min_chars: int = 40,
        max_chars: int = 400,
        per_chapter: int = 4,
        max_claims: int = 40,
    ) -> None:
        self.min_words = min_words
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.per_chapter = per_chapter
        self.max_claims = max_claims

    def _score(self, text: str) -> float:
        low = f" {text.lower()} "
        hits = sum(1 for m in _ASSERTIVE if m in low)
        words = len(_WORD.findall(text))
        return hits * 2.0 + min(words, 60) / 60.0

    def extract(self, book_id: str, sentences: list[Sentence]) -> list[Claim]:
        by_chapter: dict[str, list[Sentence]] = {}
        for s in sentences:
            if not (self.min_chars <= len(s.text) <= self.max_chars):
                continue
            if len(_WORD.findall(s.text)) < self.min_words:
                continue
            by_chapter.setdefault(s.chapter, []).append(s)

        kept: list[Sentence] = []
        for chapter, items in by_chapter.items():
            ranked = sorted(items, key=lambda s: (-self._score(s.text), s.start))
            kept.extend(ranked[: self.per_chapter])
        kept.sort(key=lambda s: s.start)
        kept = kept[: self.max_claims]

        claims: list[Claim] = []
        for s in kept:
            h = hashlib.blake2b(
                f"{book_id}:{s.start}:{s.end}".encode("utf-8"), digest_size=8
            ).hexdigest()
            claims.append(
                Claim(f"{book_id}#{h}", book_id, s.chapter, s.text, s.start, s.end)
            )
        return claims
