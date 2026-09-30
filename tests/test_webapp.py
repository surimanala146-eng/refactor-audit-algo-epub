"""La webapp tient en UNE page : un dépôt, un bouton, un flux, des résultats."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from webapp.server import app  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def live():
    """Client à boucle d'événements unique (nécessaire pour le flux SSE)."""
    with TestClient(app) as c:
        yield c
HTML = (ROOT / "webapp" / "static" / "index.html").read_text("utf-8")


def test_page_unique_sans_onglets():
    assert "data-tab" not in HTML
    assert 'role="tablist"' not in HTML
    assert HTML.count("<section") <= 3


def test_un_seul_bouton_lancer():
    buttons = re.findall(r"<button[^>]*>([^<]*)</button>", HTML)
    assert "Lancer" in [b.strip() for b in buttons]


def test_health_declare_zero_arbitrage():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["arbitrage_humain"] is False


def test_routes_arbitrage_absentes():
    for path in ("/api/runs/x/review", "/api/runs/x/resume"):
        assert client.get(path).status_code in (404, 405)


def test_un_seul_appel_depose_et_lance(live, tmp_path: Path):
    files = [
        ("files", ("a.txt", b"LA METHODE\n\n" + b"La methode critique repose sur une analyse des sources et une chronologie precise des faits. " * 6, "text/plain")),
        ("files", ("b.txt", b"LA METHODE\n\n" + b"La methode critique repose sur une analyse des sources et une chronologie precise des faits. " * 6, "text/plain")),
    ]
    r = live.post("/api/run", files=files)
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    finals = []
    with live.stream("GET", f"/api/run/{run_id}/events") as stream:
        for line in stream.iter_lines():
            if '"final"' in line:
                finals.append(line)
                break
    assert finals

    res = live.get(f"/api/run/{run_id}/result")
    assert res.status_code == 200
    assert res.json()["manifest"]["arbitrage_humain"] is False
    assert live.get(f"/api/run/{run_id}/export.zip").status_code == 200


def test_run_sans_fichier_refuse_sans_bloquer():
    assert client.post("/api/run", data={"dossier": ""}).status_code == 400
