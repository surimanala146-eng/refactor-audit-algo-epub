"""Taxonomie à 6 familles, avec incertitude explicite (FAM?)."""
from __future__ import annotations

from domain.models import FAMILIES
from retrieval.vector_index import DeterministicHashEmbedding, cosine

PROTOTYPES: dict[str, str] = {
    "FAM1": "foi croyance preuve témoignage révélation démonstration certitude doute religieux",
    "FAM2": "chronologie date année siècle époque période antérieur postérieur datation calendrier",
    "FAM3": "méthode protocole démarche analyse critère procédure étape règle méthodologie",
    "FAM4": "critique objection réfutation contradiction erreur polémique controverse désaccord",
    "FAM5": "dictionnaire lexique mot terme sens définition étymologie traduction vocabulaire",
    "FAM6": "politique pouvoir état gouvernement autorité loi institution société contexte",
}


class FamilyClassifier:
    name = "FamilyClassifier"

    def __init__(self, embedder: DeterministicHashEmbedding | None = None) -> None:
        self.embedder = embedder or DeterministicHashEmbedding()
        self._protos = {k: self.embedder.embed(v) for k, v in PROTOTYPES.items()}

    def classify(self, centroid: list[float]) -> tuple[str, float]:
        """Renvoie (famille, confiance). Sous 0.60 -> FAM? (jamais d'humain)."""
        if not centroid:
            return "FAM?", 0.0
        scored = sorted(
            ((cosine(centroid, p), fam) for fam, p in self._protos.items()),
            reverse=True,
        )
        best, fam = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        # Confiance = netteté de la séparation entre le 1er et le 2e prototype.
        confidence = max(0.0, min(1.0, 0.5 + (best - second) * 2.0 + best))
        if confidence < 0.60:
            return "FAM?", round(confidence, 4)
        return fam, round(confidence, 4)

    @staticmethod
    def label(fam: str) -> str:
        return FAMILIES.get(fam, fam)
