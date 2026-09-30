"""Modèle de données de la matrice algorithmique v2.

Invariant produit : deux états de validation seulement — PUBLIE / ELIMINE.
Aucun état « à arbitrer » : le pipeline ne s'arrête jamais pour un humain.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


def slugify(text: str, max_len: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].strip("-")) or "sans-titre"


class ValidationState(str, Enum):
    PUBLIE = "PUBLIE"
    ELIMINE = "ELIMINE"


class RelationKind(str, Enum):
    SOUTIENT = "soutient"
    CONTREDIT = "contredit"
    CONTEXTE = "contexte"
    SANS_RAPPORT = "sans_rapport"


@dataclass(frozen=True)
class Source:
    book_id: str
    path: str
    author: str
    title: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Claim:
    prop_id: str
    book_id: str
    chapter: str
    text: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Embedding:
    prop_id: str
    vector: list[float]


@dataclass
class Relation:
    source_prop: str
    target_prop: str
    kind: RelationKind
    confidence: float
    cosine: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class Evidence:
    book_id: str
    author: str
    title: str
    prop_id: str
    chapter: str
    quote: str
    start: int
    end: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Score:
    n_sources: int
    confidence: float
    palier: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Card:
    card_id: str
    slug: str
    title: str
    family: str
    family_confidence: float
    score: Score
    state: ValidationState
    evidence: list[Evidence]
    relations: list[Relation]
    eliminated_reason: str | None = None

    @property
    def family_label(self) -> str:
        return self.family if self.family_confidence >= 0.60 else "FAM?"

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "slug": self.slug,
            "title": self.title,
            "family": self.family_label,
            "family_confidence": round(self.family_confidence, 4),
            "score": self.score.to_dict(),
            "state": self.state.value,
            "eliminated_reason": self.eliminated_reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "relations": [r.to_dict() for r in self.relations],
        }


FAMILIES: dict[str, str] = {
    "FAM1": "Foi et preuve",
    "FAM2": "Chronologie",
    "FAM3": "Méthode",
    "FAM4": "Critique",
    "FAM5": "Dictionnaire / lexique",
    "FAM6": "Contexte politique",
    "FAM?": "Famille incertaine",
}
