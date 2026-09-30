"""Configuration du pipeline : YAML optionnel, défauts intégrés.

Aucune configuration n'est obligatoire. Si configs/pipeline.yaml est absent ou
illisible, les défauts ci-dessous s'appliquent et le manifest le signale.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "candidats": {
        "cos_min": 0.25,
        "auto_calibrage": True,
        "min_paires": 12,
        "fallback_percentile": 0.98,
        "cos_plancher": 0.10,
    },
    "relations": {
        "sans_rapport_max": 0.08,
        "bande_grise": [0.35, 0.65],
        "concurrence": 8,
    },
    "clustering": {"confiance_min": 0.5, "min_livres": 2},
    "publication": {"confiance_min": 0.60, "min_sources": 2},
    "paliers": {"P3": 2, "P4": 3, "P5": 5},
    "extraction": {
        "min_words": 8,
        "min_chars": 40,
        "max_chars": 400,
        "par_chapitre": 4,
        "max_claims_par_livre": 40,
    },
    "embeddings": {"dim": 256},
}

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "pipeline.yaml"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _parse_yaml(text: str) -> dict[str, Any]:
    try:  # PyYAML si disponible
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception:
        pass
    # Repli stdlib : mini-parseur pour le sous-ensemble utilisé ici.
    root: dict[str, Any] = {}
    current: dict[str, Any] = root
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, raw = line.strip().partition(":")
        raw = raw.split("#", 1)[0].strip()
        if indent == 0:
            if raw == "":
                current = root.setdefault(key, {})
            else:
                root[key] = _scalar(raw)
                current = root
        else:
            current[key] = _scalar(raw)
    return root


def _scalar(raw: str) -> Any:
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    low = raw.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw.strip("'\"")


@dataclass
class PipelineConfig:
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULTS)))
    source: str = "defauts_integres"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PipelineConfig":
        candidate = Path(path) if path else DEFAULT_CONFIG_PATH
        if candidate.is_file():
            try:
                merged = _deep_merge(DEFAULTS, _parse_yaml(candidate.read_text("utf-8")))
                return cls(merged, str(candidate))
            except Exception as exc:  # règle auto : on continue sur les défauts
                return cls(json.loads(json.dumps(DEFAULTS)), f"defauts_integres (YAML illisible: {exc})")
        return cls(json.loads(json.dumps(DEFAULTS)), "defauts_integres")

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node
