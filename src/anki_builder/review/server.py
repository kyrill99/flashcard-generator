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
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..anki import push as anki_push
from ..config import Config
from ..db import connect as db_connect
from ..db import queries
from ..models import Candidate
from ..pipeline import cards
from ..tatoeba import audio

_STATIC = Path(__file__).parent / "static"

# Loopback names the local review server trusts as its own origin/host. Used to
# defend the unauthenticated local API against DNS-rebinding (Host allowlist)
# and CSRF (cross-site state-changing requests).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_only(raw: str) -> str:
    """Strip the port (and IPv6 brackets) from a Host/netloc value."""
    raw = raw.strip()
    if raw.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        return raw[1 : raw.index("]")] if "]" in raw else raw.strip("[]")
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


def _resolve_allowed_hosts(host: str) -> set[str] | None:
    """Host allowlist for the bind address; ``None`` disables the Host check.

    Loopback binds get rebinding protection. A specific LAN bind keeps the check
    but adds that address (so the operator's own requests work) and warns. A
    ``0.0.0.0`` bind can't predict the external host, so the Host check is off
    (CSRF/Origin protection still applies) and we warn loudly.
    """
    if host in _LOOPBACK_HOSTS or host == "":
        return set(_LOOPBACK_HOSTS)
    if host == "0.0.0.0":
        print(
            "WARNING: binding 0.0.0.0 exposes the review API (no authentication) "
            "to your network — anyone who can reach this port can push/delete cards."
        )
        return None
    print(
        f"WARNING: binding non-loopback host {host!r}; the review API has no "
        "authentication. Only do this on a trusted network."
    )
    return set(_LOOPBACK_HOSTS) | {host}


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


def create_app(cfg: Config, *, allowed_hosts: set[str] | None = None) -> FastAPI:
    """Build the review app bound to the corpus DB / config in `cfg`.

    The app serves an *unauthenticated* local API, so a guard middleware defends
    it against the two ways a browser could reach it unintentionally: a Host
    allowlist blocks DNS-rebinding, and a same-site check on state-changing
    methods blocks CSRF. Pass ``allowed_hosts`` to enable the Host check
    (``run_server`` does this with the loopback set); ``None`` leaves it off but
    keeps the CSRF check. Non-browser clients (curl, the test client) send
    neither ``Sec-Fetch-Site`` nor ``Origin`` and are allowed through.
    """
    app = FastAPI(title="anki-builder review")
    target = cfg.languages.target_lang
    hosts = set(_LOOPBACK_HOSTS) if allowed_hosts is None else allowed_hosts

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        # Anti-DNS-rebinding: the Host the browser used must be one we expect.
        if allowed_hosts is not None:
            if _host_only(request.headers.get("host", "")) not in hosts:
                return JSONResponse({"detail": "host not allowed"}, status_code=400)
        # CSRF: reject cross-site state-changing requests.
        if request.method not in _SAFE_METHODS:
            site = request.headers.get("sec-fetch-site")
            if site is not None:
                if site not in ("same-origin", "none"):
                    return JSONResponse(
                        {"detail": "cross-site request blocked"}, status_code=403
                    )
            else:  # older clients without Fetch Metadata: fall back to Origin.
                origin = request.headers.get("origin")
                if origin and _host_only(urlparse(origin).netloc) not in hosts:
                    return JSONResponse(
                        {"detail": "cross-origin request blocked"}, status_code=403
                    )
        return await call_next(request)

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
        # The shared resolver handles both a Tatoeba clip (download+cache) and a
        # fallback row's cached TTS file, so a reviewer can hear either.
        path = anki_push.media_path_for_row(row, cfg)
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
            # Preserve a human-edited gloss; else repopulate from the dictionary.
            gloss = (row.get("fields") or {}).get("WordTranslation") or queries.gloss_for(
                c, row["word"]
            )
            fields = cards.build_card_fields(
                row["word"], cand, target_lang=target,
                word_translation=gloss, flag=row.get("flag") or "",
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
            row = _require(c, row_id)
            status = row.get("status")
            if status in ("deleted", "pushed", "needs_fallback"):
                raise HTTPException(status_code=409, detail=f"cannot push a {status} row")
            if status == "pending":
                # A one-click "Push now" is itself the human approval (D2 gate):
                # promote to accepted so push_accepted's status filter passes.
                queries.set_status(c, row_id, "accepted")
                c.commit()
            summary = anki_push.push_accepted(
                c, cfg, row_ids=[row_id], dry_run=False, force=False, log=lambda *_: None
            )
            c.commit()
            status_code = 200 if not summary.errors else 502
            return JSONResponse(summary.as_dict(), status_code=status_code)
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
    allowed_hosts = _resolve_allowed_hosts(host)
    print(f"Review app on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(
        create_app(cfg, allowed_hosts=allowed_hosts),
        host=host,
        port=port,
        log_level="warning",
    )
