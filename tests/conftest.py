"""Shared fixtures: a small in-memory Tatoeba corpus (no network).

The data is hand-picked to exercise each D9 tier, the D7a fan-out collapse, the
audio+translation filter, and ranking — see the docstrings on each test.
"""

from __future__ import annotations

import sqlite3

import pytest

from anki_builder import db
from anki_builder.stemming import fold_accents, stem_word, stems_blob

TARGET = "spa"
BASE = "eng"

# (id, text) — Spanish sentences. Chosen so:
#   1 has token `como` (not `cómodo`); 2 has `cómodo`; 3 has enclitic `cómelo`.
#   1 carries TWO English translations (fan-out collapse test).
#   4 has a translation but NO audio; 5 has audio but NO translation (filter).
#   6 short + native audio vs 7 long + non-native (ranking).
#   8 and 9 are identical text differing only by native vs non-native (boost).
_SPA = {
    1: "Yo como una manzana.",
    2: "El sofá es muy cómodo.",
    3: "¡Cómelo, por favor!",
    4: "Esto no tiene audio.",
    5: "Sin traducción aquí.",
    6: "El gato duerme.",
    7: "El gato negro duerme en la cama grande toda la tarde.",
    8: "Tengo un perro.",
    9: "Tengo un perro.",
}

# (id, text) — English sentences. 102 is a longer alternate translation of 1.
# 999 is unreferenced (would be filtered out by a real ingest).
_ENG = {
    101: "I eat an apple.",
    102: "I am eating an apple.",
    103: "This has no audio.",
    104: "The cat sleeps.",
    105: "The black cat sleeps in the big bed all afternoon.",
    106: "The sofa is very comfortable.",
    107: "Eat it, please.",
    108: "I have a dog.",
    109: "I have a dog.",
}

# spa_id -> eng_id translation links (id 5 deliberately has none).
_LINKS = [(1, 101), (1, 102), (2, 106), (3, 107), (4, 103),
          (6, 104), (7, 105), (8, 108), (9, 109)]

# spa sentences that have audio (id 4 deliberately has none), with contributor.
_AUDIO = {1: "foo", 2: "foo", 3: "foo", 5: "foo",
          6: "nat", 7: "foo", 8: "nat", 9: "foo"}

# Native-speaker signal (D13): `nat` is a spa native (level 5); `foo` is not.
_USER_LANGS = [("nat", "spa", "5"), ("foo", "spa", "3")]

# FreeDict glossary (headword, gloss, pos). `comer`/`comida`/`como` all stem to
# `com`, so the stem-fallback tier of gloss_for must prefer the verb `comer`:
# `comía` (absent as a headword) → stem `com` → "to eat", not "food"/"as, like".
_GLOSSARY = [
    ("comer", "to eat", "verb"),
    ("comida", "food", "noun"),
    ("como", "as, like", "conj"),
    ("gato", "cat", "noun"),
    ("rápido", "fast", "adj"),  # accented headword → accent-fold lookup test
]


def seed_corpus(conn: sqlite3.Connection) -> None:
    for sid, text in _SPA.items():
        conn.execute(
            "INSERT INTO sentences (id, lang, text, text_fold) VALUES (?,?,?,?)",
            (sid, TARGET, text, fold_accents(text)),
        )
        conn.execute(
            "INSERT INTO sentences_fts (rowid, text, stems) VALUES (?,?,?)",
            (sid, text, stems_blob(text)),
        )
    for sid, text in _ENG.items():
        conn.execute(
            "INSERT INTO sentences (id, lang, text, text_fold) VALUES (?,?,?,NULL)",
            (sid, BASE, text),
        )
    conn.executemany(
        "INSERT INTO links (sentence_id, translation_id) VALUES (?,?)", _LINKS
    )
    conn.executemany(
        "INSERT INTO audio (sentence_id, audio_id, username, license, attribution_url)"
        " VALUES (?,?,?,?,?)",
        [(sid, 5000 + sid, user, "CC BY 2.0 FR", None) for sid, user in _AUDIO.items()],
    )
    conn.executemany(
        "INSERT INTO user_languages (username, lang, level) VALUES (?,?,?)",
        _USER_LANGS,
    )
    conn.executemany(
        "INSERT INTO glossary (headword, headword_fold, headword_stem, gloss, pos)"
        " VALUES (?,?,?,?,?)",
        [(hw, fold_accents(hw.lower()), stem_word(hw), gloss, pos)
         for hw, gloss, pos in _GLOSSARY],
    )
    conn.commit()


@pytest.fixture
def corpus() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    seed_corpus(conn)
    yield conn
    conn.close()
