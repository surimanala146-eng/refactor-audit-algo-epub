"""Commande one-shot : livres -> résultats. Zéro question, zéro saisie.

    python -m orchestration <dossier_livres> <dossier_sortie>

Tout est optionnel sauf les deux dossiers. Le programme n'attend JAMAIS
d'entrée clavier : stdin n'est jamais lu.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orchestration.pipeline import PHASES, run_once


def _make_reporter(quiet: bool):
    state = {"phase": None}

    def report(phase: str, pct: float, msg: str) -> None:
        if quiet:
            return
        if state["phase"] != phase:
            state["phase"] = phase
            idx = PHASES.index(phase) + 1 if phase in PHASES else 0
            print(f"\n[{idx}/{len(PHASES)}] {phase}", file=sys.stderr, flush=True)
        bar = "#" * int(pct * 24)
        print(f"  {bar:<24} {pct*100:5.1f}%  {msg[:90]}", file=sys.stderr, flush=True)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestration",
        description="Matrice algorithmique v2 — un dossier de livres en entrée, "
        "manifest.json + fiches .md en sortie. Aucune étape humaine.",
    )
    parser.add_argument("dossier_livres", help="dossier contenant les .txt (.md/.epub/.pdf acceptés)")
    parser.add_argument("dossier_sortie", help="dossier de résultats (créé si absent)")
    parser.add_argument("--config", default=None, help="YAML de seuils (défaut : configs/pipeline.yaml)")
    parser.add_argument("--cache", default=None, help="chemin du cache SQLite d'extraction")
    parser.add_argument("--quiet", action="store_true", help="pas de progression sur stderr")
    parser.add_argument("--json", action="store_true", help="imprime le manifest sur stdout")
    args = parser.parse_args(argv)

    books = Path(args.dossier_livres).expanduser()
    if not books.is_dir():
        print(f"Erreur : {books} n'est pas un dossier.", file=sys.stderr)
        return 2

    result = run_once(
        books,
        args.dossier_sortie,
        config_path=args.config,
        progress=_make_reporter(args.quiet),
        cache_path=Path(args.cache) if args.cache else None,
    )

    if args.json:
        import json

        print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
    else:
        c = result.manifest["compteurs"]
        print("")
        print(f"Terminé — run {result.run_id} en {result.manifest['duree_s']} s")
        print(f"  livres          : {c['livres']}")
        print(f"  propositions    : {c['propositions']}")
        print(f"  candidats       : {c['candidats']}  (seuil effectif "
              f"{result.manifest['seuils']['candidat_cos_min_effectif']})")
        print(f"  fiches publiées : {c['fiches_publiees']}  (éliminées : {c['fiches_eliminees']})")
        print(f"  résultats       : {result.out_dir}")
        print(f"                    {result.out_dir}/manifest.json")
        print(f"                    {result.out_dir}/fiches/*.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
