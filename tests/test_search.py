"""D9 tiered search: FTS exact, LIKE substring (enclitic), Snowball stem."""

from __future__ import annotations

from anki_builder.db import queries


def test_fts_exact_matches_token_not_substring(corpus):
    """`como` matches the token in #1 but NOT `cómodo` (#2) or `cómelo` (#3)."""
    result = queries.search(corpus, "como", "spa")
    assert result.tier == "fts_exact"
    assert set(result.sentence_ids) == {1}


def test_fts_exact_accent_distinct(corpus):
    """Accents are preserved: searching `cómodo` hits #2 only."""
    result = queries.search(corpus, "cómodo", "spa")
    assert result.tier == "fts_exact"
    assert set(result.sentence_ids) == {2}


def test_like_catches_enclitic(corpus):
    """`come` is not a token anywhere, so we fall to LIKE, which (accent-folded)
    finds it inside the enclitic `cómelo` (#3)."""
    result = queries.search(corpus, "come", "spa")
    assert result.tier == "like_substring"
    assert set(result.sentence_ids) == {3}


def test_stem_recovers_inflection(corpus):
    """`comer` has no exact token and no substring match, so the stem tier
    recovers `como` (#1): stem(comer) == stem(como) == 'com'."""
    result = queries.search(corpus, "comer", "spa")
    assert result.tier == "stem"
    assert set(result.sentence_ids) == {1}


def test_no_match_returns_no_tier(corpus):
    """An invented word exhausts all tiers -> fallback marker."""
    result = queries.search(corpus, "xqzzy", "spa")
    assert result.tier is None
    assert result.sentence_ids == []
