"""Matrice algorithmique v2 — orchestration one-shot.

    python -m orchestration <dossier_livres> <dossier_sortie>
"""
from orchestration.config import PipelineConfig
from orchestration.pipeline import (
    PHASES,
    REGLES_AUTOMATIQUES,
    PipelineResult,
    SemanticEvidencePipeline,
    run_once,
)

__all__ = [
    "PHASES",
    "REGLES_AUTOMATIQUES",
    "PipelineConfig",
    "PipelineResult",
    "SemanticEvidencePipeline",
    "run_once",
]
__version__ = "0.3.0"
