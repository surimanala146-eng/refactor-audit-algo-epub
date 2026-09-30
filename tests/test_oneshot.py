"""Critère d'acceptation : N fichiers .txt -> UNE commande -> dossier de résultats."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestration import PHASES, REGLES_AUTOMATIQUES, run_once  # noqa: E402
from orchestration.__main__ import main  # noqa: E402
from orchestration.config import PipelineConfig  # noqa: E402

CORPUS = [
    ("alpha.txt", "LA METHODE\n\n" + " ".join(
        ["La méthode critique repose sur une analyse des sources et une chronologie précise des faits établis."] * 6)),
    ("beta.txt", "LA METHODE\n\n" + " ".join(
        ["La méthode critique repose sur une analyse des sources et une chronologie précise des faits établis."] * 6)),
    ("gamma.txt", "AUTRE\n\n" + " ".join(
        ["Le dictionnaire donne le sens du mot et son étymologie dans la langue ancienne considérée."] * 6)),
]


@pytest.fixture()
def books(tmp_path: Path) -> Path:
    d = tmp_path / "livres"
    d.mkdir()
    for name, text in CORPUS:
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_une_commande_produit_le_dossier(books: Path, tmp_path: Path):
    out = tmp_path / "sortie"
    code = main([str(books), str(out), "--quiet"])
    assert code == 0
    assert (out / "manifest.json").is_file()
    assert (out / "cards_index.json").is_file()
    assert (out / "README.md").is_file()
    assert (out / "export.zip").is_file()
    assert (out / "fiches").is_dir()


def test_module_en_sous_processus_sans_stdin(books: Path, tmp_path: Path):
    """Aucune saisie : le process tourne avec stdin fermé et sort en 0."""
    out = tmp_path / "sortie"
    proc = subprocess.run(
        [sys.executable, "-m", "orchestration", str(books), str(out), "--quiet"],
        cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "manifest.json").is_file()


def test_aucun_arbitrage_humain(books: Path, tmp_path: Path):
    res = run_once(books, tmp_path / "out")
    m = res.manifest
    assert m["arbitrage_humain"] is False
    assert m["pending_review"] is False
    assert m["regles_automatiques"] == REGLES_AUTOMATIQUES
    out = Path(m["dossier_sortie"])
    assert not list(out.rglob("review_queue.jsonl"))
    assert not list(out.rglob("decisions.jsonl"))


def test_etats_de_validation_binaires(books: Path, tmp_path: Path):
    res = run_once(books, tmp_path / "out")
    assert all(c.state.value in {"PUBLIE", "ELIMINE"} for c in res.cards)


def test_onze_phases_emises(books: Path, tmp_path: Path):
    seen: list[str] = []
    run_once(books, tmp_path / "out", progress=lambda p, pct, m: seen.append(p))
    assert set(PHASES) <= set(seen)


def test_fiches_publiees_sur_corpus_convergent(books: Path, tmp_path: Path):
    res = run_once(books, tmp_path / "out")
    assert res.manifest["compteurs"]["fiches_publiees"] >= 1
    card = res.published[0]
    assert card.score.n_sources >= 2
    assert card.score.confidence >= 0.60
    md = (Path(res.manifest["dossier_sortie"]) / "fiches" / f"{card.slug}.md")
    assert md.is_file() and card.title in md.read_text("utf-8")


def test_dossier_vide_ne_bloque_pas(tmp_path: Path):
    empty = tmp_path / "vide"
    empty.mkdir()
    res = run_once(empty, tmp_path / "out")
    assert res.manifest["compteurs"]["livres"] == 0
    assert "explication_zero_fiche" in res.manifest


def test_fichier_illisible_est_ignore_sans_pause(books: Path, tmp_path: Path):
    (books / "casse.pdf").write_bytes(b"\x00 pas un pdf")
    res = run_once(books, tmp_path / "out")
    assert len(res.manifest["sources_ignorees"]) == 1


def test_config_absente_utilise_les_defauts(books: Path, tmp_path: Path):
    cfg = PipelineConfig.load(tmp_path / "inexistant.yaml")
    assert cfg.get("publication", "confiance_min") == 0.60
    res = run_once(books, tmp_path / "out", config_path=tmp_path / "inexistant.yaml")
    assert res.manifest["config_source"].startswith("defauts_integres")


def test_manifest_json_valide_et_reproductible(books: Path, tmp_path: Path):
    a = run_once(books, tmp_path / "a").manifest
    b = run_once(books, tmp_path / "b").manifest
    for m in (a, b):
        json.dumps(m)
    assert a["compteurs"] == b["compteurs"]
    assert a["distribution_paliers"] == b["distribution_paliers"]


def test_preuves_verifiables_positions_et_sha(books: Path, tmp_path: Path):
    from ingestion.loader import read_reference_text

    res = run_once(books, tmp_path / "out")
    for card in res.published:
        for ev in card.evidence:
            src = next(s for s in res.manifest["sources"] if s["book_id"] == ev.book_id)
            text = read_reference_text(Path(src["path"]))
            assert text[ev.start : ev.end].strip() == ev.quote.strip()
            assert ev.sha256 == src["sha256"]
