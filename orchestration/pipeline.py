"""Entonnoir ingestion -> manifest EN UNE PASSE, sans arbitrage humain.

Invariant absolu : aucune phase n'attend une décision, une validation ou une
saisie. Toute ambiguïté est résolue par une règle automatique documentée
(voir `REGLES_AUTOMATIQUES`) et tracée dans le manifest.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from domain.models import (
    Card,
    Claim,
    Evidence,
    Relation,
    RelationKind,
    Score,
    Source,
    ValidationState,
    FAMILIES,
    slugify,
)
from extraction.claim_extractor import NullExtractionModel
from ingestion.loader import SUFFIXES, load_source
from ingestion.segmenter import prepare_book
from orchestration.config import PipelineConfig
from providers_impl import LexicalOverlapRelation
from retrieval.vector_index import DeterministicHashEmbedding, cosine, mean_vector
from taxonomy.family_classifier import FamilyClassifier

PHASES = [
    "ingestion",
    "segmentation",
    "extraction",
    "embeddings",
    "candidats",
    "relations",
    "clustering",
    "scoring",
    "synthese",
    "audit",
    "manifest",
]

REGLES_AUTOMATIQUES = [
    "Fichier illisible, vide ou de suffixe non supporté -> ignoré, tracé dans le manifest.",
    "Collision d'identifiant de livre -> suffixe _2, _3, ... automatique.",
    "Auteur/titre absents du nom de fichier -> auteur « Inconnu », titre = nom du fichier.",
    "Confiance de relation en bande grise [0.35;0.65[ -> CONTEXTE (zone_grise_auto), exclue du clustering.",
    "Trop peu de paires candidates au seuil fixe -> seuil recalibré sur le percentile observé (auto_calibrage), tracé dans le manifest.",
    "Famille sous 0.60 de confiance -> FAM? (incertaine), la fiche reste publiable.",
    "Fiche sous 2 sources, sous 0.60 de confiance, ou avec un span invalide -> ELIMINE (jamais soumise à un humain).",
    "Aucune fiche publiable -> le run se termine en succès avec 0 fiche et le manifest l'explique.",
]


@dataclass
class PipelineResult:
    run_id: str
    out_dir: Path
    manifest: dict[str, Any]
    cards: list[Card]

    @property
    def published(self) -> list[Card]:
        return [c for c in self.cards if c.state is ValidationState.PUBLIE]


class _Cache:
    """Mémo d'extraction SQLite, best-effort (toute erreur est ignorée)."""

    def __init__(self, path: Path | None) -> None:
        self.conn: sqlite3.Connection | None = None
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path))
            self.conn.execute("CREATE TABLE IF NOT EXISTS memo (k TEXT PRIMARY KEY, v TEXT)")
            self.conn.commit()
        except Exception:
            self.conn = None

    def get(self, key: str) -> Any | None:
        if not self.conn:
            return None
        try:
            row = self.conn.execute("SELECT v FROM memo WHERE k=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    def put(self, key: str, value: Any) -> None:
        if not self.conn:
            return
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO memo (k, v) VALUES (?, ?)", (key, json.dumps(value))
            )
            self.conn.commit()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


class SemanticEvidencePipeline:
    """Pipeline complet : dossier de livres -> manifest.json + fiches .md."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        progress: Callable[[str, float, str], None] | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.cfg = config or PipelineConfig.load()
        self._progress = progress or (lambda phase, pct, msg: None)
        self.cache = _Cache(cache_path)
        dim = int(self.cfg.get("embeddings", "dim", default=256))
        gris = self.cfg.get("relations", "bande_grise", default=[0.35, 0.65])
        self.embedder = DeterministicHashEmbedding(dim=dim)
        self.extractor = NullExtractionModel(
            min_words=int(self.cfg.get("extraction", "min_words", default=8)),
            min_chars=int(self.cfg.get("extraction", "min_chars", default=40)),
            max_chars=int(self.cfg.get("extraction", "max_chars", default=400)),
            per_chapter=int(self.cfg.get("extraction", "par_chapitre", default=4)),
            max_claims=int(self.cfg.get("extraction", "max_claims_par_livre", default=40)),
        )
        self.relator = LexicalOverlapRelation(
            sans_rapport_max=float(self.cfg.get("relations", "sans_rapport_max", default=0.08)),
            gris_min=float(gris[0]),
            gris_max=float(gris[1]),
        )
        self.family = FamilyClassifier(self.embedder)

    # ------------------------------------------------------------------ utils
    def _emit(self, phase: str, pct: float, msg: str) -> None:
        try:
            self._progress(phase, round(pct, 4), msg)
        except Exception:
            pass

    # ------------------------------------------------------------------- main
    def run(self, books_dir: str | Path, out_dir: str | Path) -> PipelineResult:
        started = time.time()
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        books_dir = Path(books_dir).expanduser().resolve()
        out_dir = Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "fiches").mkdir(exist_ok=True)

        notes: list[str] = []
        ignored: list[dict[str, str]] = []

        # 1. INGESTION -------------------------------------------------------
        self._emit("ingestion", 0.0, f"Lecture de {books_dir}")
        files = sorted(
            p for p in books_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES
        )
        sources: list[Source] = []
        texts: dict[str, str] = {}
        used: set[str] = set()
        for i, path in enumerate(files, 1):
            try:
                src, text = load_source(path, used)
                if len(text.strip()) < 200:
                    ignored.append({"fichier": str(path), "motif": "texte trop court (<200 caractères)"})
                    continue
                sources.append(src)
                texts[src.book_id] = text
            except Exception as exc:  # règle auto : on ignore, on n'interrompt pas
                ignored.append({"fichier": str(path), "motif": f"{type(exc).__name__}: {exc}"})
            self._emit("ingestion", i / max(len(files), 1), f"{path.name}")
        self._emit("ingestion", 1.0, f"{len(sources)} livre(s) retenu(s), {len(ignored)} ignoré(s)")

        # 2. SEGMENTATION ----------------------------------------------------
        segments: dict[str, Any] = {}
        for i, src in enumerate(sources, 1):
            chapters, sentences = prepare_book(texts[src.book_id])
            segments[src.book_id] = (chapters, sentences)
            self._emit(
                "segmentation", i / max(len(sources), 1),
                f"{src.book_id}: {len(chapters)} chapitre(s), {len(sentences)} phrase(s)",
            )
        self._emit("segmentation", 1.0, "segmentation terminée")

        # 3. EXTRACTION ------------------------------------------------------
        claims: list[Claim] = []
        for i, src in enumerate(sources, 1):
            key = f"claims:{src.book_id}:{src.sha256}"
            cached = self.cache.get(key)
            if cached:
                book_claims = [Claim(**c) for c in cached]
            else:
                book_claims = self.extractor.extract(src.book_id, segments[src.book_id][1])
                self.cache.put(key, [c.to_dict() for c in book_claims])
            claims.extend(book_claims)
            self._emit("extraction", i / max(len(sources), 1), f"{src.book_id}: {len(book_claims)} proposition(s)")
        self._emit("extraction", 1.0, f"{len(claims)} proposition(s) au total")

        # 4. EMBEDDINGS ------------------------------------------------------
        vectors: dict[str, list[float]] = {}
        for i, c in enumerate(claims, 1):
            vectors[c.prop_id] = self.embedder.embed(c.text)
            if i % 25 == 0 or i == len(claims):
                self._emit("embeddings", i / max(len(claims), 1), f"{i}/{len(claims)} vecteurs")
        self._emit("embeddings", 1.0, f"{len(vectors)} vecteur(s) (dim {self.embedder.dim})")

        # 5. CANDIDATS (paires inter-livres) ---------------------------------
        by_id = {c.prop_id: c for c in claims}
        pairs: list[tuple[str, str, float]] = []
        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                a, b = claims[i], claims[j]
                if a.book_id == b.book_id:
                    continue
                cos = cosine(vectors[a.prop_id], vectors[b.prop_id])
                if cos > 0:
                    pairs.append((a.prop_id, b.prop_id, cos))
        pairs.sort(key=lambda p: -p[2])

        cos_min = float(self.cfg.get("candidats", "cos_min", default=0.25))
        candidates = [p for p in pairs if p[2] >= cos_min]
        seuil_effectif = cos_min
        if (
            bool(self.cfg.get("candidats", "auto_calibrage", default=True))
            and len(candidates) < int(self.cfg.get("candidats", "min_paires", default=12))
            and pairs
        ):
            pct = float(self.cfg.get("candidats", "fallback_percentile", default=0.98))
            floor = float(self.cfg.get("candidats", "cos_plancher", default=0.10))
            want = max(
                int(self.cfg.get("candidats", "min_paires", default=12)),
                int(round(len(pairs) * (1.0 - pct))),
            )
            take = min(want, len(pairs))
            seuil_effectif = max(floor, round(pairs[take - 1][2], 4))
            candidates = [p for p in pairs if p[2] >= seuil_effectif]
            notes.append(
                f"auto_calibrage: seuil candidat abaissé de {cos_min} à {seuil_effectif} "
                f"(percentile {pct} / minimum {want} paires, plancher {floor}) — "
                "règle automatique documentée, aucune décision humaine."
            )
        self._emit(
            "candidats", 1.0,
            f"{len(candidates)} paire(s) candidate(s) sur {len(pairs)} (seuil {seuil_effectif})",
        )

        # 6. RELATIONS -------------------------------------------------------
        relations: list[Relation] = []
        grey = 0
        for i, (pa, pb, cos) in enumerate(candidates, 1):
            kind, conf, rel_notes = self.relator.score(by_id[pa].text, by_id[pb].text)
            if "zone_grise_auto" in rel_notes:
                grey += 1
            relations.append(Relation(pa, pb, kind, conf, round(cos, 4), rel_notes))
            if i % 50 == 0 or i == len(candidates):
                self._emit("relations", i / max(len(candidates), 1), f"{i}/{len(candidates)} relations")
        self._emit("relations", 1.0, f"{len(relations)} relation(s), dont {grey} en zone grise (auto -> contexte)")

        # 7. CLUSTERING (union-find sur soutient/contredit) ------------------
        conf_min = float(self.cfg.get("clustering", "confiance_min", default=0.5))
        min_books = int(self.cfg.get("clustering", "min_livres", default=2))
        parent: dict[str, str] = {c.prop_id: c.prop_id for c in claims}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[ry] = rx

        linking = [
            r for r in relations
            if r.kind in (RelationKind.SOUTIENT, RelationKind.CONTREDIT) and r.confidence >= conf_min
        ]
        for r in linking:
            union(r.source_prop, r.target_prop)

        groups: dict[str, list[str]] = {}
        for pid in parent:
            groups.setdefault(find(pid), []).append(pid)
        clusters = [
            sorted(members)
            for members in groups.values()
            if len({by_id[m].book_id for m in members}) >= min_books
        ]
        clusters.sort(key=lambda m: (-len(m), m[0]))
        self._emit("clustering", 1.0, f"{len(clusters)} groupe(s) multi-livres")

        # 8. SCORING ---------------------------------------------------------
        paliers = self.cfg.get("paliers", default={"P3": 2, "P4": 3, "P5": 5})
        cards: list[Card] = []
        for n, members in enumerate(clusters, 1):
            member_claims = [by_id[m] for m in members]
            books = sorted({c.book_id for c in member_claims})
            rels = [
                r for r in relations
                if r.source_prop in set(members) and r.target_prop in set(members)
            ]
            confs = [r.confidence for r in rels if r.kind in (RelationKind.SOUTIENT, RelationKind.CONTREDIT)]
            confidence = round(sum(confs) / len(confs), 4) if confs else 0.0
            n_sources = len(books)
            if n_sources >= int(paliers.get("P5", 5)):
                palier = "P5+"
            elif n_sources >= int(paliers.get("P4", 3)):
                palier = "P4"
            elif n_sources >= int(paliers.get("P3", 2)):
                palier = "P3"
            else:
                palier = "P1"
            centroid = mean_vector([vectors[m] for m in members])
            fam, fam_conf = self.family.classify(centroid)
            title = _title_from(member_claims)
            src_by_id = {s.book_id: s for s in sources}
            evidence = [
                Evidence(
                    book_id=c.book_id,
                    author=src_by_id[c.book_id].author,
                    title=src_by_id[c.book_id].title,
                    prop_id=c.prop_id,
                    chapter=c.chapter,
                    quote=c.text,
                    start=c.start,
                    end=c.end,
                    sha256=src_by_id[c.book_id].sha256,
                )
                for c in member_claims
            ]
            cards.append(
                Card(
                    card_id=f"card_{n:04d}",
                    slug=f"{n:03d}-{slugify(title)}",
                    title=title,
                    family=fam,
                    family_confidence=fam_conf,
                    score=Score(n_sources, confidence, palier),
                    state=ValidationState.PUBLIE,
                    evidence=evidence,
                    relations=rels,
                )
            )
            self._emit("scoring", n / max(len(clusters), 1), f"{title} ({palier}, {n_sources} sources)")
        self._emit("scoring", 1.0, f"{len(cards)} fiche(s) candidate(s)")

        # 9. AUDIT (portes de qualité, automatiques) -------------------------
        pub_conf = float(self.cfg.get("publication", "confiance_min", default=0.60))
        pub_src = int(self.cfg.get("publication", "min_sources", default=2))
        for card in cards:
            reasons: list[str] = []
            if card.score.n_sources < pub_src:
                reasons.append(f"moins de {pub_src} sources")
            if card.score.confidence < pub_conf:
                reasons.append(f"confiance moyenne {card.score.confidence} < {pub_conf}")
            for ev in card.evidence:
                ref = texts.get(ev.book_id, "")
                if not (0 <= ev.start < ev.end <= len(ref)) or ref[ev.start : ev.end].strip() != ev.quote.strip():
                    reasons.append(f"span invalide pour {ev.book_id}")
                    break
            if reasons:
                card.state = ValidationState.ELIMINE
                card.eliminated_reason = " ; ".join(reasons)
        published = [c for c in cards if c.state is ValidationState.PUBLIE]
        self._emit("audit", 1.0, f"{len(published)} publiée(s) / {len(cards)} candidate(s)")

        # 10. SYNTHESE (Markdown + preuves JSON) -----------------------------
        fiches_dir = out_dir / "fiches"
        for old in fiches_dir.glob("*"):
            if old.is_file():
                old.unlink()
        index: list[dict[str, Any]] = []
        for i, card in enumerate(published, 1):
            md_path = fiches_dir / f"{card.slug}.md"
            md_path.write_text(_render_markdown(card, run_id), encoding="utf-8")
            (fiches_dir / f"{card.slug}.evidence.json").write_text(
                json.dumps(card.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            index.append(
                {
                    "card_id": card.card_id,
                    "slug": card.slug,
                    "title": card.title,
                    "family": card.family_label,
                    "family_label": FAMILIES.get(card.family_label, card.family_label),
                    "palier": card.score.palier,
                    "n_sources": card.score.n_sources,
                    "confidence": card.score.confidence,
                    "markdown": f"fiches/{card.slug}.md",
                }
            )
            self._emit("synthese", i / max(len(published), 1), card.title)
        (out_dir / "cards_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "eliminees.json").write_text(
            json.dumps(
                [c.to_dict() for c in cards if c.state is ValidationState.ELIMINE],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._emit("synthese", 1.0, f"{len(published)} fiche(s) écrite(s)")

        # 11. MANIFEST -------------------------------------------------------
        manifest = {
            "run_id": run_id,
            "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duree_s": round(time.time() - started, 3),
            "mode": "one-shot (aucune étape humaine)",
            "arbitrage_humain": False,
            "pending_review": False,
            "dossier_livres": str(books_dir),
            "dossier_sortie": str(out_dir),
            "config_source": self.cfg.source,
            "fournisseurs": {
                "extraction": self.extractor.name,
                "embeddings": self.embedder.name,
                "relations": self.relator.name,
                "taxonomie": self.family.name,
            },
            "seuils": {
                "candidat_cos_min_configure": cos_min,
                "candidat_cos_min_effectif": seuil_effectif,
                "bande_grise": self.cfg.get("relations", "bande_grise", default=[0.35, 0.65]),
                "clustering_confiance_min": conf_min,
                "publication_confiance_min": pub_conf,
                "publication_min_sources": pub_src,
                "paliers": paliers,
            },
            "extraction": self.cfg.get("extraction", default={}),
            "embeddings": {"dim": self.embedder.dim},
            "sources": [s.to_dict() for s in sources],
            "sources_ignorees": ignored,
            "compteurs": {
                "livres": len(sources),
                "propositions": len(claims),
                "paires_inter_livres": len(pairs),
                "candidats": len(candidates),
                "relations": len(relations),
                "zone_grise": grey,
                "clusters": len(clusters),
                "fiches_publiees": len(published),
                "fiches_eliminees": len(cards) - len(published),
            },
            "distribution_paliers": dict(Counter(c.score.palier for c in published)),
            "distribution_familles": dict(Counter(c.family_label for c in published)),
            "regles_automatiques": REGLES_AUTOMATIQUES,
            "notes_run": notes,
            "avertissement": (
                "Mode local : extraction lexicale, embeddings sac-de-mots haché, relations "
                "par recouvrement. Similarité ≠ attribution ≠ soutien documentaire ≠ validité "
                "scientifique. Les fiches sont des regroupements traçables, pas des vérités."
            ),
            "fiches": index,
        }
        if not published:
            manifest["explication_zero_fiche"] = (
                "Aucune fiche n'a franchi les portes de qualité (≥2 sources, confiance ≥ "
                f"{pub_conf}). Le run est un succès : il n'y a simplement rien de publiable "
                "dans ce corpus avec ces seuils."
            )
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_readme(out_dir, manifest)
        _write_zip(out_dir)
        self._emit("manifest", 1.0, f"manifest.json écrit dans {out_dir}")
        self.cache.close()
        return PipelineResult(run_id, out_dir, manifest, cards)


# --------------------------------------------------------------------- helpers
_TITLE_STOP = set(
    "le la les de des du un une et en au aux que qui dans pour par sur avec est sont ce "
    "cette ces il elle ils elles on ne pas plus se sa son ses leur leurs mais donc or ni "
    "car tout tous cela comme aussi".split()
)


def _title_from(claims: Iterable[Claim]) -> str:
    from retrieval.vector_index import tokenize

    counter: Counter[str] = Counter()
    for c in claims:
        counter.update(t for t in tokenize(c.text) if t not in _TITLE_STOP)
    words = [w for w, _ in counter.most_common(5)]
    if not words:
        return "Regroupement sans mot saillant"
    return " ".join(words).capitalize()


def _render_markdown(card: Card, run_id: str) -> str:
    lines = [
        f"# {card.title}",
        "",
        f"- **Fiche** : `{card.card_id}` — run `{run_id}`",
        f"- **Palier** : {card.score.palier}",
        f"- **Famille** : {card.family_label} — {FAMILIES.get(card.family_label, '')}"
        + (" *(incertaine)*" if card.family_label == "FAM?" else ""),
        f"- **Sources** : {card.score.n_sources}",
        f"- **Confiance moyenne** : {card.score.confidence}",
        "",
        "## Propositions et preuves",
        "",
    ]
    for ev in card.evidence:
        lines += [
            f"### {ev.title} — {ev.author}",
            "",
            f"> {ev.quote}",
            "",
            f"- Chapitre : {ev.chapter}",
            f"- Positions dans le texte de référence : {ev.start}–{ev.end}",
            f"- SHA256 de la source : `{ev.sha256[:16]}…`",
            f"- prop_id : `{ev.prop_id}`",
            "",
        ]
    lines += ["## Relations retenues", ""]
    if card.relations:
        lines.append("| A | B | relation | confiance | cosinus |")
        lines.append("|---|---|---|---|---|")
        for r in card.relations:
            lines.append(
                f"| `{r.source_prop[-8:]}` | `{r.target_prop[-8:]}` | {r.kind.value} | "
                f"{r.confidence} | {r.cosine} |"
            )
    else:
        lines.append("_Aucune relation interne._")
    lines += [
        "",
        "---",
        "",
        "_Fiche produite automatiquement, sans arbitrage humain. Similarité ≠ soutien "
        "documentaire ≠ validité scientifique._",
        "",
    ]
    return "\n".join(lines)


def _write_readme(out_dir: Path, manifest: dict[str, Any]) -> None:
    c = manifest["compteurs"]
    lines = [
        "# Résultat de la matrice algorithmique v2",
        "",
        f"- Run : `{manifest['run_id']}` — {manifest['genere_le']} ({manifest['duree_s']} s)",
        f"- Livres traités : {c['livres']} (ignorés : {len(manifest['sources_ignorees'])})",
        f"- Propositions : {c['propositions']} — candidats : {c['candidats']} — relations : {c['relations']}",
        f"- Fiches publiées : **{c['fiches_publiees']}** (éliminées : {c['fiches_eliminees']})",
        "",
        "## Contenu du dossier",
        "",
        "| Fichier | Rôle |",
        "|---|---|",
        "| `manifest.json` | reproductibilité : seuils, compteurs, sources, règles automatiques |",
        "| `fiches/*.md` | les fiches, lisibles directement |",
        "| `fiches/*.evidence.json` | preuves structurées (positions + SHA256) |",
        "| `cards_index.json` | index des fiches |",
        "| `eliminees.json` | fiches écartées par les portes de qualité, avec le motif |",
        "| `export.zip` | tout le dossier, prêt à partager |",
        "",
        "## Fiches",
        "",
    ]
    if manifest["fiches"]:
        for f in manifest["fiches"]:
            lines.append(
                f"- [{f['title']}]({f['markdown']}) — {f['palier']}, {f['n_sources']} sources, "
                f"{f['family']}"
            )
    else:
        lines.append("_" + manifest.get("explication_zero_fiche", "Aucune fiche.") + "_")
    lines += ["", "_Aucune étape humaine n'est intervenue dans la production de ce dossier._", ""]
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _write_zip(out_dir: Path) -> None:
    zip_path = out_dir / "export.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file() and p != zip_path:
                zf.write(p, p.relative_to(out_dir).as_posix())


def run_once(
    books_dir: str | Path,
    out_dir: str | Path,
    config_path: str | Path | None = None,
    progress: Callable[[str, float, str], None] | None = None,
    cache_path: Path | None = None,
) -> PipelineResult:
    """Entrée unique : un dossier de livres -> un dossier de résultats."""
    cfg = PipelineConfig.load(config_path)
    return SemanticEvidencePipeline(cfg, progress, cache_path).run(books_dir, out_dir)
