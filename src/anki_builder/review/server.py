"""FastAPI review app (D2): the mandatory human gate before any push.

Audio-centric review — you *hear* the native clip and can *swap* among the
ranked candidate sentences before accepting (the whole reason D2 chose a web UI
over a terminal). Sync endpoints each open their own SQLite connection (WAL is
on), so the single-user local server stays simple. The push endpoints reuse
`anki.push.push_accepted`, the same path the CLI uses.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..anki import push as anki_push
from ..anki.connect import AnkiClient
from ..config import Config
from ..db import connect as db_connect
from ..db import queries
from ..models import Candidate
from ..pipeline import cards
from ..tatoeba import audio

_STATIC = Path(__file__).parent / "static"


class SwapBody(BaseModel):
    candidate_index: int


class EditBody(BaseModel):
    fields: dict


def _candidate_from_entry(entry: dict) -> Candidate:
    """Rebuild a Candidate from a stored candidates_json entry (for swap)."""
    return Candidate(
        sentence_id=entry["sentence_id"],
        spa_text=entry["spa_text"],
        translation=entry["translation"],
        audio_id=entry.get("audio_id"),
        username=entry.get("username"),
        license=entry.get("license"),
        is_native=bool(entry.get("is_native")),
    )


def create_app(cfg: Config) -> FastAPI:
    """Build the review app bound to the corpus DB / config in `cfg`."""
    app = FastAPI(title="anki-builder review")
    target = cfg.languages.target_lang

    def conn() -> sqlite3.Connection:
        return db_connect(cfg.paths.db_path)

    def _require(c: sqlite3.Connection, row_id: int) -> dict:
        row = queries.get_row(c, row_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"row {row_id} not found")
        return row

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/queue")
    def get_queue(status: str | None = None):
        c = conn()
        try:
            return {"rows": queries.list_queue(c, status)}
        finally:
            c.close()

    @app.get("/api/audio/{row_id}")
    def get_audio(row_id: int):
        c = conn()
        try:
            row = _require(c, row_id)
        finally:
            c.close()
        sid = row.get("chosen_sentence_id")
        if not sid:
            raise HTTPException(status_code=404, detail="row has no audio sentence")
        audio_id = anki_push._chosen_audio_id(row)
        path = audio.download_audio(
            sid, lang=target, cache_dir=cfg.paths.media_cache, audio_id=audio_id
        )
        if path is None:
            raise HTTPException(status_code=404, detail="audio unavailable")
        return FileResponse(path, media_type="audio/mpeg")

    @app.post("/api/queue/{row_id}/swap")
    def swap(row_id: int, body: SwapBody):
        c = conn()
        try:
            row = _require(c, row_id)
            candidates = row.get("candidates") or []
            if not 0 <= body.candidate_index < len(candidates):
                raise HTTPException(status_code=400, detail="candidate index out of range")
            cand = _candidate_from_entry(candidates[body.candidate_index])
            fields = cards.build_card_fields(
                row["word"], cand, target_lang=target, flag=row.get("flag") or ""
            )
            queries.update_row(
                c,
                row_id,
                fields=fields.as_dict(),
                chosen_sentence_id=cand.sentence_id,
                audio_filename=audio.media_filename(cand.sentence_id, target),
            )
            c.commit()
            return queries.get_row(c, row_id)
        finally:
            c.close()

    @app.post("/api/queue/{row_id}/edit")
    def edit(row_id: int, body: EditBody):
        c = conn()
        try:
            row = _require(c, row_id)
            fields = {**(row.get("fields") or {}), **body.fields}
            # Keep the type-in card honest: re-blank from the edited Word/Sentence.
            fields["SentenceBlanked"] = cards.blank_sentence(
                fields.get("Sentence", ""), fields.get("Word", "")
            )
            queries.update_row(c, row_id, fields=fields)
            c.commit()
            return queries.get_row(c, row_id)
        finally:
            c.close()

    @app.post("/api/queue/{row_id}/accept")
    def accept(row_id: int):
        c = conn()
        try:
            _require(c, row_id)
            queries.set_status(c, row_id, "accepted")
            c.commit()
            return {"ok": True, "status": "accepted"}
        finally:
            c.close()

    @app.post("/api/queue/{row_id}/delete")
    def delete(row_id: int):
        c = conn()
        try:
            _require(c, row_id)
            queries.set_status(c, row_id, "deleted")
            c.commit()
            return {"ok": True, "status": "deleted"}
        finally:
            c.close()

    @app.post("/api/queue/{row_id}/push")
    def push_one(row_id: int):
        c = conn()
        try:
            _require(c, row_id)
            summary = anki_push.push_accepted(
                c, cfg, row_ids=[row_id], dry_run=False, force=False, log=lambda *_: None
            )
            c.commit()
            status = 200 if not summary.errors else 502
            return JSONResponse(summary.as_dict(), status_code=status)
        finally:
            c.close()

    @app.post("/api/push-all")
    def push_all():
        c = conn()
        try:
            summary = anki_push.push_accepted(
                c, cfg, dry_run=False, force=False, log=lambda *_: None
            )
            c.commit()
            status = 200 if not summary.errors else 502
            return JSONResponse(summary.as_dict(), status_code=status)
        finally:
            c.close()

    return app


def run_server(cfg: Config, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the review app with uvicorn (blocking)."""
    import uvicorn

    if not Path(cfg.paths.db_path).exists():
        print(
            "Corpus is empty. Run `anki-builder fetch-dumps`, `build-db`, then "
            "`run` first."
        )
    print(f"Review app on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
