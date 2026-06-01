"""End-to-end ingest from synthetic .tsv dumps (no network), then a search."""

from __future__ import annotations

from pathlib import Path

from anki_builder import db
from anki_builder.db import build as db_build
from anki_builder.db import queries

SPA_SENTENCES = "1\tspa\tYo como una manzana.\n2\tspa\tEl sofá es muy cómodo.\n"
# 999 is unreferenced by any link and must be filtered out of the base table.
ENG_SENTENCES = (
    "101\teng\tI eat an apple.\n"
    "102\teng\tI am eating an apple.\n"
    "106\teng\tThe sofa is very comfortable.\n"
    "999\teng\tUnrelated sentence.\n"
)
LINKS = "1\t101\n1\t102\n2\t106\n"
AUDIO = "1\t5001\tfoo\tCC BY 2.0 FR\thttps://x\n2\t5002\tfoo\tCC BY 2.0 FR\thttps://x\n"
USER_LANGS = "spa\t5\tnat\t\n"


def _write_dumps(dumps_dir: Path) -> None:
    dumps_dir.mkdir(parents=True, exist_ok=True)
    (dumps_dir / "spa_sentences.tsv").write_text(SPA_SENTENCES, encoding="utf-8")
    (dumps_dir / "eng_sentences.tsv").write_text(ENG_SENTENCES, encoding="utf-8")
    (dumps_dir / "spa-eng_links.tsv").write_text(LINKS, encoding="utf-8")
    (dumps_dir / "spa_sentences_with_audio.tsv").write_text(AUDIO, encoding="utf-8")
    (dumps_dir / "spa_user_languages.tsv").write_text(USER_LANGS, encoding="utf-8")


def test_build_db_counts_and_filtering(tmp_path):
    _write_dumps(tmp_path / "dumps")
    db_path = tmp_path / "tatoeba.db"

    counts = db_build.build_db(db_path, tmp_path / "dumps", log=lambda *_: None)

    assert counts.target_sentences == 2
    assert counts.base_sentences == 3  # 101, 102, 106 — NOT the unreferenced 999
    assert counts.links == 3
    assert counts.audio == 2
    assert counts.user_languages == 1


def test_build_db_then_search_and_filter(tmp_path):
    _write_dumps(tmp_path / "dumps")
    db_path = tmp_path / "tatoeba.db"
    db_build.build_db(db_path, tmp_path / "dumps", log=lambda *_: None)

    conn = db.connect(db_path)
    try:
        result = queries.search(conn, "como", "spa")
        assert result.tier == "fts_exact"
        assert set(result.sentence_ids) == {1}

        candidates = queries.filtered_candidates(
            conn, result.sentence_ids, target_lang="spa", base_lang="eng"
        )
        assert len(candidates) == 1
        assert candidates[0].translation == "I eat an apple."
    finally:
        conn.close()


def test_build_db_is_idempotent(tmp_path):
    """Re-running build-db rebuilds the corpus without doubling rows."""
    _write_dumps(tmp_path / "dumps")
    db_path = tmp_path / "tatoeba.db"
    db_build.build_db(db_path, tmp_path / "dumps", log=lambda *_: None)
    counts = db_build.build_db(db_path, tmp_path / "dumps", log=lambda *_: None)
    assert counts.target_sentences == 2
    assert counts.links == 3
