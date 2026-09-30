"""Fournisseurs de modèles. Baseline locale réelle ; distants explicitement absents."""
from __future__ import annotations

from domain.models import RelationKind
from retrieval.vector_index import tokenize


class ProviderIndisponible(RuntimeError):
    """Levée quand un fournisseur distant est demandé sans être câblé/configuré."""


_NEGATION = (
    "ne ", "n'", " pas ", " jamais ", " aucun", " nullement", " contrairement",
    " faux", " erreur", " réfute", " conteste", " nie ", " sans ",
)


class LexicalOverlapRelation:
    """Relation par recouvrement lexical (Jaccard + couverture).

    Règles automatiques, documentées, sans intervention humaine :
      score < sans_rapport_max          -> SANS_RAPPORT
      bande grise [gris_min ; gris_max[ -> CONTEXTE (annoté zone_grise_auto)
      score >= gris_max                 -> SOUTIENT, ou CONTREDIT si l'une des
                                           deux propositions porte une négation
                                           que l'autre n'a pas.
    """

    name = "LexicalOverlapRelation"

    def __init__(
        self, sans_rapport_max: float = 0.08, gris_min: float = 0.35, gris_max: float = 0.65
    ) -> None:
        self.sans_rapport_max = sans_rapport_max
        self.gris_min = gris_min
        self.gris_max = gris_max

    @staticmethod
    def _negated(text: str) -> bool:
        low = f" {text.lower()} "
        return any(m in low for m in _NEGATION)

    def score(self, text_a: str, text_b: str) -> tuple[RelationKind, float, list[str]]:
        a, b = set(tokenize(text_a)), set(tokenize(text_b))
        if not a or not b:
            return RelationKind.SANS_RAPPORT, 0.0, ["tokens_vides"]
        inter = len(a & b)
        jaccard = inter / len(a | b)
        coverage = inter / min(len(a), len(b))
        value = (jaccard + coverage) / 2.0
        notes: list[str] = []
        if value < self.sans_rapport_max:
            return RelationKind.SANS_RAPPORT, round(value, 4), notes
        if self.gris_min <= value < self.gris_max:
            notes.append("zone_grise_auto")
            return RelationKind.CONTEXTE, round(value, 4), notes
        if value < self.gris_min:
            notes.append("sous_bande_grise_auto")
            return RelationKind.CONTEXTE, round(value, 4), notes
        if self._negated(text_a) != self._negated(text_b):
            notes.append("marqueur_negation")
            return RelationKind.CONTREDIT, round(value, 4), notes
        return RelationKind.SOUTIENT, round(value, 4), notes


def _unavailable(label: str):
    def factory(*_a, **_k):
        raise ProviderIndisponible(
            f"{label} n'est pas câblé dans cette baseline : aucun appel distant fictif."
        )

    return factory


LLMExtraction = _unavailable("LLMExtraction")
EmbeddingAPI = _unavailable("EmbeddingAPI")
NLIRelation = _unavailable("NLIRelation")
