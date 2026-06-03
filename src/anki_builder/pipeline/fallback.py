"""Generate an LLM fallback card when Tatoeba has no usable sentence (D4).

Reached from `cli.cmd_run`'s `needs_fallback` branch (the seam `select.py` marks)
only when `cfg.llm.fallback_enabled` AND a key are present. Produces a flagged
card enqueued `status=pending`, so it still passes the mandatory D2 review gate.

TTS failure is non-fatal: the card is enqueued silent (`audio_filename=None`) but
still flagged, never aborting the word. The LLM call itself raising propagates to
`cmd_run`, which warns and falls through to the marked-only `needs_fallback` row.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..config import Config
from ..db import queries
from ..llm.client import LLMClient
from ..llm.tts import TTSClient
from ..models import CardFields
from ..tatoeba import audio
from . import cards


@dataclass
class FallbackResult:
    fields: CardFields
    audio_filename: str | None


def generate_fallback(
    word: str,
    *,
    cfg: Config,
    conn: sqlite3.Connection,
    llm_client: LLMClient | None = None,
    tts_client: TTSClient | None = None,
) -> FallbackResult:
    """Build a flagged fallback card (sentence + TTS audio) for `word`."""
    target = cfg.languages.target_lang
    base = cfg.languages.base_lang

    llm = llm_client or LLMClient(cfg.llm)
    fb = llm.generate_sentence(word, target_lang=target, base_lang=base)

    # Prefer the LLM's gloss; fall back to the offline FreeDict dictionary.
    gloss = fb.gloss or queries.gloss_for(conn, word)

    # Synthesize TTS into the same media cache push/serve read from. Non-fatal.
    audio_filename: str | None = audio.fallback_media_filename(word, target)
    tts = tts_client or TTSClient(cfg.tts, api_key=cfg.llm.api_key, base_url=cfg.llm.base_url)
    try:
        tts.synthesize_to(fb.spa_text, cfg.paths.media_cache / audio_filename)
    except Exception:  # noqa: BLE001 — silent card beats no card; still flagged
        audio_filename = None

    fields = cards.build_fallback_fields(
        word,
        fb.spa_text,
        fb.translation,
        word_translation=gloss,
        audio_filename=audio_filename,
    )
    return FallbackResult(fields=fields, audio_filename=audio_filename)
