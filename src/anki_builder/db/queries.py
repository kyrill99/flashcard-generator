"""Local corpus queries: D9 tiered search, D7a candidate filter, review queue.

All per-word work is local SQL (D5) — the Tatoeba API is never touched at
runtime.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from ..models import Candidate, SearchResult
from ..stemming import fold_accents, stem_word

# Cascade order (D9). First tier that yields hits wins; only when all three are
# empty does the caller escalate to the LLM fallback.
TIER_ORDER = ("fts_exact", "like_substring", "stem")


def _fts_quote(term: str) -> str:
    """Wrap a single token as an FTS5 phrase, escaping embedded quotes."""
    return '"' + term.replace('"', '""') + '"'


def _like_escape(term: str) -> str:
    """Escape LIKE metacharacters so a literal word matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fts_exact_ids(conn: sqlite3.Connection, word: str) -> list[int]:
    """Tier 1: exact token match on the `text` column (accents preserved)."""
    query = f"text:{_fts_quote(word.lower())}"
    cur = conn.execute(
        "SELECT rowid FROM sentences_fts WHERE sentences_fts MATCH ?", (query,)
    )
    return [r[0] for r in cur]


def like_substring_ids(
    conn: sqlite3.Connection, word: str, target_lang: str
) -> list[int]:
    """Tier 2: accent-folded substring (catches enclitics like `cómelo`)."""
    pattern = f"%{_like_escape(fold_accents(word))}%"
    cur = conn.execute(
        "SELECT id FROM sentences WHERE lang = ? AND text_fold LIKE ? ESCAPE '\\'",
        (target_lang, pattern),
    )
    return [r[0] for r in cur]


def stem_ids(conn: sqlite3.Connection, word: str) -> list[int]:
    """Tier 3: Snowball-stem token match (recovers inflection like `comer`)."""
    query = f"stems:{_fts_quote(stem_word(word))}"
    cur = conn.execute(
        "SELECT rowid FROM sentences_fts WHERE sentences_fts MATCH ?", (query,)
    )
    return [r[0] for r in cur]


def search(conn: sqlite3.Connection, word: str, target_lang: str) -> SearchResult:
    """Run the D9 cascade; return the first non-empty tier's sentence ids."""
    tiers = {
        "fts_exact": lambda: fts_exact_ids(conn, word),
        "like_substring": lambda: like_substring_ids(conn, word, target_lang),
        "stem": lambda: stem_ids(conn, word),
    }
    for tier in TIER_ORDER:
        ids = tiers[tier]()
        if ids:
            return SearchResult(word=word, tier=tier, sentence_ids=ids)
    return SearchResult(word=word, tier=None, sentence_ids=[])


def native_usernames(conn: sqlite3.Connection, target_lang: str) -> set[str]:
    """Usernames self-rated native (skill level 5) in the target language (D13).

    Empty when the optional user_languages dump was not loaded — ranking then
    degrades to length/simplicity only.
    """
    cur = conn.execute(
        "SELECT DISTINCT username FROM user_languages "
        "WHERE lang = ? AND level = '5'",
        (target_lang,),
    )
    return {r[0] for r in cur if r[0]}


def filtered_candidates(
    conn: sqlite3.Connection,
    sentence_ids: Sequence[int],
    *,
    target_lang: str,
    base_lang: str,
) -> list[Candidate]:
    """Apply the audio+translation filter, collapsed one-row-per-spa-id (D7a).

    A scalar subquery selects the shortest base-language translation as the
    display value, so a spa sentence with N English translations yields exactly
    one candidate (no fan-out). Only sentences that have BOTH audio AND a
    base-language translation survive.
    """
    if not sentence_ids:
        return []

    natives = native_usernames(conn, target_lang)

    # Stage candidate ids in a temp table to avoid IN(...) parameter limits when
    # the stem tier returns thousands of ids.
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _cand (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM _cand")
    conn.executemany(
        "INSERT OR IGNORE INTO _cand (id) VALUES (?)",
        ((int(i),) for i in sentence_ids),
    )

    cur = conn.execute(
        """
        SELECT s.id            AS sentence_id,
               s.text          AS spa_text,
               a.audio_id      AS audio_id,
               a.username      AS username,
               a.license       AS license,
               (SELECT e.text
                  FROM links l JOIN sentences e ON e.id = l.translation_id
                 WHERE l.sentence_id = s.id AND e.lang = :base
                 ORDER BY length(e.text) ASC, e.id ASC
                 LIMIT 1)      AS translation
          FROM _cand c
          JOIN sentences s ON s.id = c.id AND s.lang = :target
          JOIN audio a      ON a.sentence_id = s.id
         WHERE EXISTS (SELECT 1
                         FROM links l JOIN sentences e ON e.id = l.translation_id
                        WHERE l.sentence_id = s.id AND e.lang = :base)
        """,
        {"target": target_lang, "base": base_lang},
    )

    candidates: list[Candidate] = []
    for row in cur:
        candidates.append(
            Candidate(
                sentence_id=row["sentence_id"],
                spa_text=row["spa_text"],
                translation=row["translation"],
                audio_id=row["audio_id"],
                username=row["username"],
                license=row["license"],
                is_native=bool(row["username"] and row["username"] in natives),
            )
        )
    return candidates


# --- glossary (the L1 word gloss / WordTranslation field) ------------------


def _is_verb(pos: str | None, headword: str) -> bool:
    """Heuristic: the POS names a verb, or the headword has an infinitive ending.

    Inflection misses are overwhelmingly verbs, so this biases the stem-fallback
    tier toward the verb headword when a stem collides (e.g. com -> comer/comida).
    """
    if pos and "v" in pos.lower():  # "verb", "v", "vt", "vi", "vb" ...
        return True
    return headword.lower().endswith(("ar", "er", "ir"))


def gloss_for(conn: sqlite3.Connection, word: str) -> str:
    """The short L1 gloss for a target word, inflection-proof. "" when unknown.

    Two-tier cascade mirroring the D9 search cascade and `cards._match_priority`:

    1. **Exact-fold** (precise) — folded-key match nails lemmas / uninflected
       inputs (comer -> "to eat", gato -> "cat", comida -> "food"); `rowid` order
       returns the first/primary sense.
    2. **Stem-fallback** (recovers inflection) — only when tier 1 misses. All
       headwords sharing the Snowball stem are ranked in Python by
       ``(0 if verb else 1, len(headword))`` — prefer a verb headword, then the
       shortest — so comía (stem `com`) -> comer -> "to eat", beating comida/como.

    Reuses `fold_accents`/`stem_word` so the queried keys match the indexed ones.
    """
    folded = fold_accents(word.lower())
    row = conn.execute(
        "SELECT gloss FROM glossary WHERE headword_fold = ? ORDER BY rowid LIMIT 1",
        (folded,),
    ).fetchone()
    if row is not None:
        return row[0]

    stem = stem_word(word)
    rows = conn.execute(
        "SELECT gloss, pos, headword FROM glossary WHERE headword_stem = ?",
        (stem,),
    ).fetchall()
    if not rows:
        return ""
    best = min(
        rows, key=lambda r: (0 if _is_verb(r["pos"], r["headword"]) else 1, len(r["headword"]))
    )
    return best["gloss"]


def translations_for(
    conn: sqlite3.Connection, sentence_id: int, base_lang: str
) -> list[str]:
    """All base-language translations of a spa sentence, shortest first (D7a)."""
    cur = conn.execute(
        """
        SELECT e.text
          FROM links l JOIN sentences e ON e.id = l.translation_id
         WHERE l.sentence_id = ? AND e.lang = ?
         ORDER BY length(e.text) ASC, e.id ASC
        """,
        (sentence_id, base_lang),
    )
    return [r[0] for r in cur]


# --- review_queue (D11) ----------------------------------------------------


def enqueue(
    conn: sqlite3.Connection,
    *,
    word: str,
    status: str,
    chosen_sentence_id: int | None,
    candidates: list[dict] | None,
    fields: dict | None,
    audio_filename: str | None,
    flag: str | None,
) -> int:
    """Insert a review_queue row; returns its id. Caller commits."""
    cur = conn.execute(
        """
        INSERT INTO review_queue
            (word, status, chosen_sentence_id, candidates_json,
             fields_json, audio_filename, flag)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            word,
            status,
            chosen_sentence_id,
            json.dumps(candidates, ensure_ascii=False) if candidates is not None else None,
            json.dumps(fields, ensure_ascii=False) if fields is not None else None,
            audio_filename,
            flag,
        ),
    )
    return int(cur.lastrowid)


def word_in_queue(conn: sqlite3.Connection, word: str) -> bool:
    """Whether a (non-deleted) review_queue row already exists for this word."""
    row = conn.execute(
        "SELECT 1 FROM review_queue WHERE word = ? AND status != 'deleted' LIMIT 1",
        (word,),
    ).fetchone()
    return row is not None


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Decode a review_queue row, parsing the JSON columns to objects."""
    d = dict(row)
    d["candidates"] = json.loads(d["candidates_json"]) if d["candidates_json"] else []
    d["fields"] = json.loads(d["fields_json"]) if d["fields_json"] else {}
    d.pop("candidates_json", None)
    d.pop("fields_json", None)
    return d


def list_queue(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """Return review_queue rows (JSON columns decoded), newest first.

    `status=None` returns everything except soft-deleted rows; pass an explicit
    status (e.g. ``"pending"``, ``"accepted"``) to filter.
    """
    if status is None:
        cur = conn.execute(
            "SELECT * FROM review_queue WHERE status != 'deleted' ORDER BY id DESC"
        )
    else:
        cur = conn.execute(
            "SELECT * FROM review_queue WHERE status = ? ORDER BY id DESC", (status,)
        )
    return [_row_to_dict(r) for r in cur]


def get_row(conn: sqlite3.Connection, row_id: int) -> dict | None:
    """Fetch a single review_queue row by id (JSON columns decoded)."""
    row = conn.execute(
        "SELECT * FROM review_queue WHERE id = ?", (row_id,)
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    fields: dict | None = None,
    chosen_sentence_id: int | None = None,
    audio_filename: str | None = None,
    flag: str | None = None,
) -> None:
    """Patch the mutable columns of a review_queue row (review edits/swap).

    Only the keyword arguments that are not ``None`` are written, so a caller can
    update just the fields, just the chosen sentence, etc. Caller commits.
    """
    sets: list[str] = []
    params: list[object] = []
    if fields is not None:
        sets.append("fields_json = ?")
        params.append(json.dumps(fields, ensure_ascii=False))
    if chosen_sentence_id is not None:
        sets.append("chosen_sentence_id = ?")
        params.append(chosen_sentence_id)
    if audio_filename is not None:
        sets.append("audio_filename = ?")
        params.append(audio_filename)
    if flag is not None:
        sets.append("flag = ?")
        params.append(flag)
    if not sets:
        return
    params.append(row_id)
    conn.execute(
        f"UPDATE review_queue SET {', '.join(sets)} WHERE id = ?", params
    )


def set_status(conn: sqlite3.Connection, row_id: int, status: str) -> None:
    """Set a review_queue row's status (e.g. accepted/deleted/pushed). Caller commits."""
    conn.execute(
        "UPDATE review_queue SET status = ? WHERE id = ?", (status, row_id)
    )


def mark_pushed(conn: sqlite3.Connection, row_id: int) -> None:
    """Mark a row as pushed to Anki (terminal state). Caller commits."""
    set_status(conn, row_id, "pushed")
