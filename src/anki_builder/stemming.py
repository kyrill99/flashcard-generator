"""Spanish tokenisation + Snowball stemming (D9 tier 3).

Shared by the ingest (`db/build.py`, which writes the `stems` FTS column) and by
search (`db/queries.py`, which stems the query word). Using one code path
guarantees indexed stems and query stems are produced identically, so the stem
tier actually matches.
"""

from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer

# Runs of letters only (Unicode-aware), so accents (á é í ó ú ü ñ) are kept and
# digits / punctuation / underscores are excluded. "cómelo", "como", "niño" are
# single tokens; "3" or "," are not tokens.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Spanish accent folding for the LIKE tier (D9 tier 2). We strip the acute
# accent off vowels and fold ü→u, but DELIBERATELY keep ñ — it is a distinct
# letter in Spanish (año "year" must not collapse onto ano). This makes `come`
# a substring of the enclitic `cómelo` (folded: `comelo`), which plain LIKE
# misses. FTS tiers 1 & 3 keep accents instead (remove_diacritics 0).
_FOLD_MAP = str.maketrans(
    {
        "á": "a", "à": "a", "ä": "a", "â": "a",
        "é": "e", "è": "e", "ë": "e", "ê": "e",
        "í": "i", "ì": "i", "ï": "i", "î": "i",
        "ó": "o", "ò": "o", "ö": "o", "ô": "o",
        "ú": "u", "ù": "u", "ü": "u", "û": "u",
    }
)


def fold_accents(text: str) -> str:
    """Lowercase + strip vowel accents (keeps ñ). Used only by the LIKE tier."""
    return text.lower().translate(_FOLD_MAP)


@lru_cache(maxsize=1)
def _spanish_stemmer():
    return snowballstemmer.stemmer("spanish")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens of a Spanish string (accents preserved)."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def stem_word(word: str) -> str:
    """Snowball stem of a single surface form (lowercased first)."""
    return _spanish_stemmer().stemWord(word.lower())


def stem_tokens(text: str) -> list[str]:
    """Stem every token in a sentence, preserving order."""
    tokens = tokenize(text)
    if not tokens:
        return []
    return _spanish_stemmer().stemWords(tokens)


def stems_blob(text: str) -> str:
    """Space-joined stems for storage in the `sentences_fts.stems` column.

    FTS5's unicode61 tokenizer re-splits this on whitespace, so a space-joined
    string is the right shape for `MATCH 'stems:<stem>'` queries.
    """
    return " ".join(stem_tokens(text))
