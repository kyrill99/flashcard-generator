"""Rank candidate sentences (D13): prefer short/simple, boost native audio.

Higher score = better. Scoring degrades gracefully when the native signal is
absent (no user_languages dump) — every candidate just has is_native=False, so
ranking falls back to pure length/simplicity, exactly as D13 specifies.
"""

from __future__ import annotations

from ..config import RankingConfig
from ..models import Candidate
from ..stemming import tokenize


def score_candidate(candidate: Candidate, cfg: RankingConfig) -> float:
    text = candidate.spa_text
    n_words = len(tokenize(text))

    penalty = cfg.length_weight * len(text) + cfg.word_count_weight * n_words
    # Out-of-band length penalty: nudge toward the ideal word-count window.
    if n_words < cfg.ideal_min_words:
        penalty += cfg.word_count_weight * (cfg.ideal_min_words - n_words)
    elif n_words > cfg.ideal_max_words:
        penalty += cfg.word_count_weight * (n_words - cfg.ideal_max_words)

    boost = cfg.native_audio_boost if candidate.is_native else 0.0
    return boost - penalty


def rank(candidates: list[Candidate], cfg: RankingConfig) -> list[Candidate]:
    """Return a new list sorted best-first, with each `.score` populated."""
    for c in candidates:
        c.score = score_candidate(c, cfg)
    # Sort by score desc; deterministic tie-breaks: shorter text, then id.
    return sorted(
        candidates,
        key=lambda c: (-c.score, len(c.spa_text), c.sentence_id),
    )
