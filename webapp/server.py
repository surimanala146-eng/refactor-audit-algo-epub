"""Console web — UNE SEULE PAGE.

Dépôt des livres -> un bouton « Lancer » -> progression SSE -> résultats
affichés automatiquement. Aucun onglet, aucune décision humaine, aucune
configuration obligatoire.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from ingestion.loader import SUFFIXES
from orchestration.pipeline import PHASES, REGLES_AUTOMATIQUES, run_once

BASE_DIR = Path(__file__).resolve().parent
STATIC = BASE_DIR / "static"
WORKSPACE = Path(__file__).resolve().parent.parent / "workspace"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("matrice")

app = FastAPI(title="Matrice Algorithmique v2 — page unique")

RUNS: dict[str, dict[str, Any]] = {}


def _run_dir(run_id: str) -> Path:
    return WORKSPACE / "runs" / run_id


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "phases": PHASES,
        "suffixes": list(SUFFIXES),
        "arbitrage_humain": False,
        "regles_automatiques": REGLES_AUTOMATIQUES,
    }


@app.post("/api/run")
async def start_run(files: list[UploadFile] = File(default=[]), dossier: str = Form(default="")):
    """UN seul appel : dépose les livres et démarre immédiatement le run."""
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    rdir = _run_dir(run_id)
    books_dir = rdir / "livres"
    out_dir = rdir / "resultats"
    books_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in files or []:
        name = Path(f.filename or "livre.txt").name
        if Path(name).suffix.lower() not in SUFFIXES:
            continue  # règle automatique : suffixe non supporté -> ignoré
        data = await f.read()
        (books_dir / name).write_bytes(data)
        count += 1

    if count == 0 and dossier.strip():
        src = Path(dossier.strip()).expanduser()
        if src.is_dir():
            for p in sorted(src.rglob("*")):
                if p.is_file() and p.suffix.lower() in SUFFIXES:
                    shutil.copy2(p, books_dir / p.name)
                    count += 1

    if count == 0:
        shutil.rmtree(rdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Aucun livre exploitable (.txt/.md/.epub/.pdf).")

    state: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "phase": PHASES[0],
        "progress": 0.0,
        "logs": [],
        "manifest": None,
        "error": None,
        "queue": asyncio.Queue(),
        "n_livres": count,
    }
    RUNS[run_id] = state
    loop = asyncio.get_running_loop()

    def progress(phase: str, pct: float, msg: str) -> None:
        idx = PHASES.index(phase) if phase in PHASES else 0
        overall = (idx + pct) / len(PHASES)
        state["phase"] = phase
        state["progress"] = round(overall, 4)
        line = f"{phase}: {msg}"
        state["logs"].append(line)
        loop.call_soon_threadsafe(
            state["queue"].put_nowait,
            {"type": "progress", "phase": phase, "progress": state["progress"], "message": msg},
        )

    async def worker() -> None:
        try:
            result = await asyncio.to_thread(
                run_once, books_dir, out_dir, None, progress, WORKSPACE / "cache.sqlite"
            )
            state["manifest"] = result.manifest
            state["status"] = "done"
            state["progress"] = 1.0
        except Exception as exc:  # jamais de pause : on échoue explicitement
            log.exception("run %s échoué", run_id)
            state["status"] = "failed"
            state["error"] = f"{type(exc).__name__}: {exc}"
        await state["queue"].put({"type": "final", "status": state["status"], "error": state["error"]})

    asyncio.create_task(worker())
    return JSONResponse({"run_id": run_id, "n_livres": count}, status_code=202)


@app.get("/api/run/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(404, "run inconnu")

    async def gen():
        yield f"data: {json.dumps({'type': 'hello', 'phases': PHASES})}\n\n"
        while True:
            try:
                evt = await asyncio.wait_for(state["queue"].get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") == "final":
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/run/{run_id}")
async def run_state(run_id: str) -> dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(404, "run inconnu")
    return {k: v for k, v in state.items() if k != "queue"}


@app.get("/api/run/{run_id}/result")
async def run_result(run_id: str) -> dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(404, "run inconnu")
    if state["status"] != "done":
        raise HTTPException(409, f"run {state['status']}")
    out = _run_dir(run_id) / "resultats"
    cards = []
    index = json.loads((out / "cards_index.json").read_text("utf-8"))
    for item in index:
        cards.append(
            {**item, "markdown_text": (out / item["markdown"]).read_text("utf-8")}
        )
    return {"manifest": state["manifest"], "cards": cards, "dossier": str(out)}


@app.get("/api/run/{run_id}/export.zip")
async def export_zip(run_id: str) -> FileResponse:
    path = _run_dir(run_id) / "resultats" / "export.zip"
    if not path.is_file():
        raise HTTPException(404, "export indisponible")
    return FileResponse(path, media_type="application/zip", filename=f"{run_id}.zip")
