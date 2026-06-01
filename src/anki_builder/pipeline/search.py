"""Word -> filtered candidate sentences (D9 cascade + D7a collapse).

Thin orchestration over `db.queries`: run the tiered search, then apply the
audio + base-translation filter that collapses to one candidate per spa id.
"""

from __future__ import annotations

import sqlite3

from ..db import queries
from ..models import Candidate, SearchResult


def find_candidates(
    conn: sqlite3.Connection,
    word: str,
    *,
    target_lang: str,
    base_lang: str,
) -> tuple[SearchResult, list[Candidate]]:
    """Return (which tier hit + raw ids, filtered one-per-sentence candidates)."""
    result = queries.search(conn, word, target_lang)
    candidates = queries.filtered_candidates(
        conn,
        result.sentence_ids,
        target_lang=target_lang,
        base_lang=base_lang,
    )
    return result, candidates
