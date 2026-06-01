"""One-time ingest of Tatoeba dumps into the local SQLite corpus (D5/D7).

Reads decompressed `.tsv` dumps from a directory (see `tatoeba/dumps.py` for the
downloader) and populates `sentences` (target + base), `links` (canonicalised
target->base), `audio`, `user_languages`, and the `sentences_fts` index with
both an exact `text` column and a Snowball `stems` column (D9 tiers 1 & 3).

Ingest order is chosen to keep the base-language table small: we load the target
sentences, then the bilingual links, then load **only** the base sentences that
those links reference (a spa->eng dump still has ~2M English sentences, the vast
majority irrelevant to our spa corpus).
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import connect
from ..stemming import fold_accents, stems_blob

# Tatoeba uses MySQL-style \N for NULL in some columns.
_NULL = "\\N"
_BATCH = 5000

# Tables we rebuild on each `build-db`; review_queue is intentionally preserved.
_CORPUS_TABLES = ("sentences", "links", "audio", "user_languages")


@dataclass
class IngestCounts:
    target_sentences: int = 0
    base_sentences: int = 0
    links: int = 0
    audio: int = 0
    user_languages: int = 0
    malformed: int = 0  # rows skipped for a non-integer id (corrupt dump line)

    def as_dict(self) -> dict[str, int]:
        return {
            "target_sentences": self.target_sentences,
            "base_sentences": self.base_sentences,
            "links": self.links,
            "audio": self.audio,
            "user_languages": self.user_languages,
            "malformed": self.malformed,
        }


def dump_paths(dumps_dir: Path, target_lang: str, base_lang: str) -> dict[str, Path]:
    """Canonical local filenames the downloader writes / the ingest reads."""
    return {
        "target_sentences": dumps_dir / f"{target_lang}_sentences.tsv",
        "base_sentences": dumps_dir / f"{base_lang}_sentences.tsv",
        "links": dumps_dir / f"{target_lang}-{base_lang}_links.tsv",
        "audio": dumps_dir / f"{target_lang}_sentences_with_audio.tsv",
        "user_languages": dumps_dir / f"{target_lang}_user_languages.tsv",
    }


def _rows(path: Path) -> Iterator[list[str]]:
    """Yield tab-split rows, skipping blanks. QUOTE_NONE: Tatoeba isn't quoted."""
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in reader:
            if row:
                yield row


def _clean(value: str | None) -> str | None:
    if value is None or value == _NULL:
        return None
    return value


def _to_int(value: str | None) -> int | None:
    """Parse an id column; return None for a missing/non-integer value.

    Lets a single corrupt dump line be skipped+counted instead of aborting the
    whole multi-million-row ingest (the source is usually clean, but one bad
    line shouldn't cost the entire build).
    """
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _batched(it: Iterable, n: int) -> Iterator[list]:
    batch: list = []
    for item in it:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def _reset_corpus(conn: sqlite3.Connection) -> None:
    for table in _CORPUS_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM sentences_fts")


def _load_target_sentences(
    conn: sqlite3.Connection, path: Path, lang: str, counts: IngestCounts
) -> set[int]:
    """Insert target sentences + FTS rows; return the set of target ids."""
    ids: set[int] = set()

    def gen() -> Iterator[tuple]:
        for row in _rows(path):
            if len(row) < 3:
                continue
            sid = _to_int(row[0])
            if sid is None:
                counts.malformed += 1
                continue
            text = row[2]
            ids.add(sid)
            yield (sid, lang, text, fold_accents(text), stems_blob(text))

    for batch in _batched(gen(), _BATCH):
        conn.executemany(
            "INSERT OR IGNORE INTO sentences (id, lang, text, text_fold) "
            "VALUES (?, ?, ?, ?)",
            [(sid, lg, txt, fold) for (sid, lg, txt, fold, _stems) in batch],
        )
        conn.executemany(
            "INSERT INTO sentences_fts (rowid, text, stems) VALUES (?, ?, ?)",
            [(sid, txt, stems) for (sid, _lg, txt, _fold, stems) in batch],
        )
    return ids


def _load_links(
    conn: sqlite3.Connection, path: Path, target_ids: set[int], counts: IngestCounts
) -> set[int]:
    """Insert canonical (target_id -> other_id) links; return referenced others.

    The bilingual dump is already pre-filtered to the language pair, but we still
    canonicalise so the target id is always `sentence_id`, guarding against
    either column order.
    """
    needed: set[int] = set()

    def gen() -> Iterator[tuple[int, int]]:
        for row in _rows(path):
            if len(row) < 2:
                continue
            a, b = _to_int(row[0]), _to_int(row[1])
            if a is None or b is None:
                counts.malformed += 1
                continue
            if a in target_ids:
                src, dst = a, b
            elif b in target_ids:
                src, dst = b, a
            else:
                continue
            needed.add(dst)
            yield (src, dst)

    for batch in _batched(gen(), _BATCH):
        conn.executemany(
            "INSERT INTO links (sentence_id, translation_id) VALUES (?, ?)", batch
        )
    return needed


def _load_base_sentences(
    conn: sqlite3.Connection,
    path: Path,
    lang: str,
    keep_ids: set[int],
    counts: IngestCounts,
) -> int:
    """Insert only base sentences referenced by links (keeps the table small)."""
    count = 0

    def gen() -> Iterator[tuple]:
        nonlocal count
        for row in _rows(path):
            if len(row) < 3:
                continue
            sid = _to_int(row[0])
            if sid is None:
                counts.malformed += 1
                continue
            if sid not in keep_ids:
                continue
            count += 1
            yield (sid, lang, row[2], None)

    for batch in _batched(gen(), _BATCH):
        conn.executemany(
            "INSERT OR IGNORE INTO sentences (id, lang, text, text_fold) "
            "VALUES (?, ?, ?, ?)",
            batch,
        )
    return count


def _load_audio(
    conn: sqlite3.Connection, path: Path, target_ids: set[int], counts: IngestCounts
) -> int:
    """Insert audio rows for target sentences. Tolerates 4- or 5-column dumps."""
    count = 0

    def parse(row: list[str]) -> tuple | None:
        if len(row) < 2:
            return None
        sid = _to_int(row[0])
        if sid is None:
            counts.malformed += 1
            return None
        if sid not in target_ids:
            return None
        # 5-col: sid, audio_id, username, license, attribution_url
        # 4-col: sid, username, license, attribution_url
        if len(row) >= 5 and (row[1].isdigit() or row[1] == _NULL):
            audio_id = _clean(row[1])
            username, license_, attr = row[2], row[3], row[4]
        else:
            audio_id = None
            username = row[1] if len(row) > 1 else None
            license_ = row[2] if len(row) > 2 else None
            attr = row[3] if len(row) > 3 else None
        return (
            sid,
            int(audio_id) if audio_id and audio_id.isdigit() else None,
            _clean(username),
            _clean(license_),
            _clean(attr),
        )

    def gen() -> Iterator[tuple]:
        nonlocal count
        for row in _rows(path):
            parsed = parse(row)
            if parsed is not None:
                count += 1
                yield parsed

    for batch in _batched(gen(), _BATCH):
        conn.executemany(
            "INSERT OR IGNORE INTO audio "
            "(sentence_id, audio_id, username, license, attribution_url) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )
    return count


def _load_user_languages(conn: sqlite3.Connection, path: Path, lang: str) -> int:
    """Insert (username, lang, level) rows for the target language (D13)."""
    count = 0

    def gen() -> Iterator[tuple]:
        nonlocal count
        for row in _rows(path):
            # Format: lang, skill_level, username, details
            if len(row) < 3:
                continue
            row_lang = row[0]
            if row_lang != lang:
                continue
            level = _clean(row[1])
            username = _clean(row[2])
            if not username:
                continue
            count += 1
            yield (username, row_lang, level)

    for batch in _batched(gen(), _BATCH):
        conn.executemany(
            "INSERT INTO user_languages (username, lang, level) VALUES (?, ?, ?)",
            batch,
        )
    return count


def build_db(
    db_path: str | Path,
    dumps_dir: str | Path,
    *,
    target_lang: str = "spa",
    base_lang: str = "eng",
    log=print,
) -> IngestCounts:
    """Ingest dumps from ``dumps_dir`` into ``db_path``. Returns row counts."""
    dumps_dir = Path(dumps_dir)
    paths = dump_paths(dumps_dir, target_lang, base_lang)

    required = ["target_sentences", "base_sentences", "links", "audio"]
    missing = [paths[k].name for k in required if not paths[k].exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing required dump file(s) in {dumps_dir}: {', '.join(missing)}. "
            "Run `anki-builder fetch-dumps` first."
        )

    conn = connect(db_path)
    counts = IngestCounts()
    try:
        conn.execute("BEGIN")
        _reset_corpus(conn)

        log(f"Loading {target_lang} sentences ...")
        target_ids = _load_target_sentences(
            conn, paths["target_sentences"], target_lang, counts
        )
        counts.target_sentences = len(target_ids)

        log(f"Loading {target_lang}-{base_lang} links ...")
        needed_base = _load_links(conn, paths["links"], target_ids, counts)
        counts.links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]

        log(f"Loading {base_lang} sentences (referenced only) ...")
        counts.base_sentences = _load_base_sentences(
            conn, paths["base_sentences"], base_lang, needed_base, counts
        )

        log("Loading audio index ...")
        counts.audio = _load_audio(conn, paths["audio"], target_ids, counts)

        if paths["user_languages"].exists():
            log("Loading user_languages (native signal) ...")
            counts.user_languages = _load_user_languages(
                conn, paths["user_languages"], target_lang
            )
        else:
            log("user_languages dump absent — native-audio boost disabled.")

        if counts.malformed:
            log(f"Skipped {counts.malformed:,} malformed row(s) (non-integer id).")

        for key, value in counts.as_dict().items():
            conn.execute(
                "INSERT OR REPLACE INTO ingest_meta (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA optimize")
        conn.close()

    return counts


if __name__ == "__main__":  # pragma: no cover - manual smoke entrypoint
    import argparse

    ap = argparse.ArgumentParser(description="Ingest Tatoeba dumps into SQLite")
    ap.add_argument("--db", required=True)
    ap.add_argument("--dumps", required=True)
    ap.add_argument("--target", default="spa")
    ap.add_argument("--base", default="eng")
    args = ap.parse_args()
    result = build_db(
        args.db, args.dumps, target_lang=args.target, base_lang=args.base
    )
    print(result.as_dict(), file=sys.stderr)
