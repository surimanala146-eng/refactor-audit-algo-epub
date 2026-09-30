# Matrice algorithmique v2 — un coup, zéro étape humaine

> Tu mets les livres `.txt`, à la sortie tu as le résultat. Une seule fois,
> pas de pages multiples, pas de question à un humain.

## 1. La commande (chemin principal)

```bash
python -m orchestration <dossier_livres> <dossier_sortie>
```

C'est tout. Aucune saisie, aucune configuration obligatoire, aucune dépendance
externe requise pour ce chemin (bibliothèque standard uniquement, Python ≥ 3.11).
`stdin` n'est jamais lu : la commande fonctionne avec l'entrée fermée, en cron
ou en CI.

Exemple :

```bash
python -m orchestration ./livres ./resultats
```

### Ce qu'on trouve dans `<dossier_sortie>`

| Fichier | Rôle |
|---|---|
| `manifest.json` | reproductibilité : seuils utilisés, compteurs, sources, règles automatiques appliquées |
| `fiches/*.md` | les fiches, ouvrables directement |
| `fiches/*.evidence.json` | preuves structurées (citations, positions, SHA256) |
| `cards_index.json` | index des fiches publiées |
| `eliminees.json` | fiches écartées par les portes de qualité, **avec le motif** |
| `README.md` | résumé lisible du run |
| `export.zip` | tout le dossier, prêt à partager |

Options (toutes facultatives) : `--config` (YAML de seuils), `--cache` (mémo
SQLite d'extraction), `--quiet`, `--json`.

## 2. L'interface web — UNE seule page

```bash
pip install -r requirements.txt
python -m uvicorn webapp.server:app --host 0.0.0.0 --port 8765
```

Une page, trois blocs qui s'enchaînent tout seuls :
**dépôt des livres** (glisser-déposer, dossier entier accepté) → **un bouton
« Lancer »** → **progression en direct** (SSE, repli automatique en sondage) →
**résultats affichés automatiquement** (fiches rendues, manifest, `export.zip`).
Zéro onglet, zéro deuxième écran, zéro validation à cocher.

API minimale : `GET /api/health`, `POST /api/run` (dépose **et** démarre en un
seul appel), `GET /api/run/{id}/events` (SSE), `GET /api/run/{id}`,
`GET /api/run/{id}/result`, `GET /api/run/{id}/export.zip`.
Aucune route d'arbitrage — elles n'existent pas et les tests le vérifient.

## 3. Invariant : jamais d'attente humaine

Le pipeline ne connaît que deux états de validation : `PUBLIE` et `ELIMINE`.
Il n'y a ni `A_ARBITRER`, ni `waiting_review`, ni `review_queue.jsonl`, ni
`resume`. Toute ambiguïté est tranchée par une **règle automatique documentée**,
listée dans `orchestration.REGLES_AUTOMATIQUES` et recopiée dans chaque
`manifest.json` :

| Ambiguïté | Règle automatique |
|---|---|
| Fichier illisible / vide / suffixe non supporté | ignoré, tracé dans `sources_ignorees` |
| Identifiant de livre en collision | suffixe `_2`, `_3`, … |
| Auteur/titre absents du nom de fichier | auteur « Inconnu », titre = nom du fichier |
| Relation en bande grise `[0.35 ; 0.65[` | `CONTEXTE` (`zone_grise_auto`), exclue du clustering |
| Trop peu de paires candidates au seuil fixe | seuil recalibré sur le percentile observé (`auto_calibrage`), tracé au manifest |
| Famille sous 0.60 de confiance | `FAM?` (incertaine), la fiche reste publiable |
| Fiche < 2 sources, confiance < 0.60, ou span invalide | `ELIMINE` avec motif |
| Aucune fiche publiable | run en **succès** avec 0 fiche + `explication_zero_fiche` |

## 4. Le pipeline (11 phases, une passe)

`ingestion → segmentation → extraction → embeddings → candidats → relations →
clustering → scoring → synthese → audit → manifest`

- **ingestion** — `.txt`/`.md` nativement, `.epub`/`.pdf` si les extras sont
  installés ; texte normalisé NFC, SHA256 figé à l'enregistrement.
- **extraction** — baseline lexicale déterministe (phrases assertives,
  ≥ 8 mots, 40–400 caractères, 4 par chapitre, 40 max par livre), mémorisée
  dans un cache SQLite indexé par `book_id + sha256`.
- **embeddings** — sac de mots haché L2-normalisé (dim 256). *Ce n'est pas un
  embedding sémantique.*
- **candidats / relations** — paires inter-livres au cosinus ≥ seuil, puis
  recouvrement lexical (Jaccard + couverture) → `soutient` / `contredit` /
  `contexte` / `sans_rapport`.
- **clustering / scoring** — union-find sur les liens de confiance ≥ 0.5,
  groupe gardé s'il couvre ≥ 2 livres ; paliers P1/P3/P4/P5+.
- **audit** — portes de qualité, positions de preuve revérifiées dans le texte
  de référence.
- **manifest** — tout ce qu'il faut pour rejouer le run à l'identique.

Les seuils vivent dans `configs/pipeline.yaml`. Si le fichier manque ou est
illisible, les défauts intégrés s'appliquent et le manifest le dit.

## 5. Honnêteté sur ce que ce n'est pas

Mode local = lexical de bout en bout. **Similarité ≠ attribution ≠ soutien
documentaire ≠ validité scientifique.** Les fiches sont des regroupements
traçables et auditables, pas des vérités établies. Aucun appel distant fictif
n'est simulé : les fournisseurs non câblés lèvent `ProviderIndisponible`.

## 6. Tests

```bash
python -m pytest -q     # 17 tests
```

Ils vérifient notamment le critère d'acceptation lui-même : N fichiers `.txt` →
une commande (lancée avec `stdin` fermé) → un dossier de résultats complet ;
l'absence de toute route/fichier d'arbitrage ; la page unique sans onglets ;
et la reproductibilité du manifest.

## 7. Documents d'origine

Les fichiers `SEIGNEUR_0*.txt` sont l'audit de reprise et la consigne client à
l'origine de cette implémentation. Ils sont conservés tels quels.
