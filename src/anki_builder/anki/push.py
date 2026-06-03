"""Push accepted review_queue rows into Anki (D10/D12).

Shared by the `push` CLI command and the review web app's push buttons. The
review gate (D2) is upstream: only rows a human marked `accepted` are eligible.
Each row: download+cache its audio -> storeMediaFile -> canAddNotes dedupe
(keyed on Word) -> addNote -> mark `pushed`. Errors are caught per row so one
bad card never aborts the batch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..db import queries
from ..tatoeba import audio
from . import model
from .connect import AnkiClient, AnkiConnectError


@dataclass
class PushSummary:
    pushed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"pushed": self.pushed, "skipped": self.skipped, "errors": self.errors}


def _chosen_audio_id(row: dict) -> int | None:
    """The audio_id of the row's chosen candidate (for the CDN 404 fallback)."""
    for c in row.get("candidates", []):
        if c.get("sentence_id") == row.get("chosen_sentence_id"):
            return c.get("audio_id")
    return None


def media_path_for_row(row: dict, cfg: Config) -> Path | None:
    """Resolve the on-disk mp3 for a review row, or None when there is none.

    Shared by push and the review audio endpoint so both treat Tatoeba and
    fallback rows the same way:

    - a Tatoeba row (`chosen_sentence_id`) → download+cache the native clip;
    - a fallback row (no sentence id, but an `audio_filename` whose TTS file is
      already cached) → that cached path, no network;
    - otherwise None (no audio: the card is pushed/served silent).
    """
    sid = row.get("chosen_sentence_id")
    if sid:
        return audio.download_audio(
            sid,
            lang=cfg.languages.target_lang,
            cache_dir=cfg.paths.media_cache,
            audio_id=_chosen_audio_id(row),
        )
    fname = row.get("audio_filename")
    if fname:
        cached = Path(cfg.paths.media_cache) / fname
        if cached.exists():
            return cached
    return None


def push_accepted(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    row_ids: list[int] | None = None,
    dry_run: bool = False,
    force: bool = False,
    client: AnkiClient | None = None,
    log=print,
) -> PushSummary:
    """Push `accepted` rows (or the given `row_ids`) to Anki. Caller commits."""
    if row_ids is None:
        rows = queries.list_queue(conn, status="accepted")
    else:
        # Explicit ids still honour the D2 gate: only `accepted` rows push
        # (unless forced), so a stray id can't re-push a deleted/pushed row or
        # slip an unreviewed `pending` one past review.
        rows = []
        for rid in row_ids:
            r = queries.get_row(conn, rid)
            if r is None:
                continue
            if not force and r.get("status") != "accepted":
                continue
            rows.append(r)

    summary = PushSummary()
    if not rows:
        log("Nothing to push (no accepted rows).")
        return summary

    client = client or AnkiClient(cfg.anki.connect_url)
    deck, model_name = cfg.anki.deck, cfg.anki.note_type

    if not dry_run:
        try:
            client.ensure_deck(deck)
            client.ensure_model(model.model_definition(model_name))
        except AnkiConnectError as exc:
            summary.errors.append(str(exc))
            log(f"ERROR: {exc}")
            return summary

    for row in rows:
        word = row.get("word", "?")
        fields = row.get("fields") or {}
        if not fields:
            summary.skipped += 1
            log(f"skipped (not reviewed / no fields): {word}")
            continue

        note = model.note_payload(deck, model_name, fields, allow_duplicate=force)

        if dry_run:
            log(f"[dry-run] would push {word}: {note['fields']}")
            if row.get("audio_filename"):
                log(f"          media: {row['audio_filename']}")
            summary.pushed += 1
            continue

        try:
            # Dedupe on Word (D12) before doing any work.
            if not force:
                can_add = client.can_add_notes([note])
                if not can_add:  # empty/short result — don't IndexError the batch
                    summary.skipped += 1
                    log(f"skipped (canAddNotes returned no result): {word}")
                    continue
                if not can_add[0]:
                    summary.skipped += 1
                    log(f"skipped (already in deck): {word}")
                    continue

            # Store the mp3 first (native clip or TTS fallback) so [sound:…] resolves.
            fname = row.get("audio_filename")
            if fname:
                path = media_path_for_row(row, cfg)
                if path is not None:
                    client.store_media_path(fname, path)
                else:
                    log(f"  warning: no audio for {word}; card pushed silent")

            client.add_note(note)
            queries.mark_pushed(conn, row["id"])
            summary.pushed += 1
            log(f"pushed: {word}")
        except (AnkiConnectError, OSError) as exc:
            summary.errors.append(f"{word}: {exc}")
            log(f"ERROR pushing {word}: {exc}")

    return summary
