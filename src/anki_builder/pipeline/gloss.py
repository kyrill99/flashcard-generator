"""Resolve a word's L1 gloss: offline FreeDict first, LLM fallback on a miss.

FreeDict's `spa-eng` is small (~4.5k headwords) and uneven, so common words
(e.g. `añadir`) often have no entry. When the dictionary misses *and* an LLM
client is supplied, we ask the model for a short gloss — covering the gap at
mining time so the review card isn't blank. The gloss stays editable in review.

The LLM client is passed in (not built here) so the caller owns the gate: it
supplies a client only when a key is set and neither `--dry-run` nor
`--no-fallback` is in play. A dict hit never triggers a network call.
"""

from __future__ import annotations

import sqlite3

from ..db import queries
from ..llm.client import LLMClient


def resolve_gloss(
    conn: sqlite3.Connection,
    word: str,
    *,
    target_lang: str = "spa",
    base_lang: str = "eng",
    llm_client: LLMClient | None = None,
) -> tuple[str, str]:
    """Return ``(gloss, source)`` where source is ``"dict"``, ``"llm"``, or ``""``.

    Dictionary first; on a miss, the LLM (if a client is given). Any LLM failure
    degrades to ``("", "")`` so mining never breaks on a gloss.
    """
    g = queries.gloss_for(conn, word)
    if g:
        return g, "dict"
    if llm_client is not None:
        try:
            g = llm_client.generate_gloss(word, target_lang=target_lang, base_lang=base_lang)
        except Exception:  # noqa: BLE001 — a gloss is optional, never fatal
            g = ""
        if g:
            return g, "llm"
    return "", ""
