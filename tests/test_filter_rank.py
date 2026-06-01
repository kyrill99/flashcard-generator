"""D7a fan-out collapse, the audio+translation filter, and D13 ranking."""

from __future__ import annotations

from anki_builder.config import RankingConfig
from anki_builder.db import queries
from anki_builder.pipeline import rank, search, select


def test_fanout_collapse_one_candidate_per_sentence(corpus):
    """#1 has TWO English translations but must yield exactly ONE candidate,
    showing the shortest translation (D7a)."""
    candidates = queries.filtered_candidates(
        corpus, [1], target_lang="spa", base_lang="eng"
    )
    assert len(candidates) == 1
    assert candidates[0].sentence_id == 1
    assert candidates[0].translation == "I eat an apple."  # shorter of the two

    alternates = queries.translations_for(corpus, 1, "eng")
    assert alternates == ["I eat an apple.", "I am eating an apple."]


def test_filter_requires_audio_and_translation(corpus):
    """#4 (translation, no audio) and #5 (audio, no translation) are excluded;
    only #1 survives."""
    candidates = queries.filtered_candidates(
        corpus, [1, 4, 5], target_lang="spa", base_lang="eng"
    )
    assert {c.sentence_id for c in candidates} == {1}


def test_ranking_prefers_short_and_native(corpus):
    """For `gato`, the short native-audio sentence (#6) outranks the long
    non-native one (#7)."""
    _result, candidates = search.find_candidates(
        corpus, "gato", target_lang="spa", base_lang="eng"
    )
    ranked = rank.rank(candidates, RankingConfig())
    assert [c.sentence_id for c in ranked] == [6, 7]
    assert ranked[0].is_native is True
    assert ranked[1].is_native is False


def test_native_boost_breaks_a_tie(corpus):
    """#8 and #9 are identical text; the native-contributed one wins on boost."""
    _result, candidates = search.find_candidates(
        corpus, "perro", target_lang="spa", base_lang="eng"
    )
    ranked = rank.rank(candidates, RankingConfig())
    assert ranked[0].sentence_id == 8
    assert ranked[0].is_native is True


def test_select_ok_and_fallback(corpus):
    """select picks #1 for a real word, and routes an invented word to
    fallback (no usable candidate)."""
    result, candidates = search.find_candidates(
        corpus, "como", target_lang="spa", base_lang="eng"
    )
    ranked = rank.rank(candidates, RankingConfig())
    chosen = select.select(result, ranked, candidates_kept=8)
    assert chosen.status == select.STATUS_OK
    assert chosen.chosen.sentence_id == 1

    result2, candidates2 = search.find_candidates(
        corpus, "xqzzy", target_lang="spa", base_lang="eng"
    )
    ranked2 = rank.rank(candidates2, RankingConfig())
    fallback = select.select(result2, ranked2, candidates_kept=8)
    assert fallback.needs_fallback
    assert fallback.chosen is None
