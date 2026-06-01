"""Pick the best candidate, or mark the word for fallback (D4 seam).

A word routes to fallback when there is no *usable* Tatoeba candidate — i.e. no
sentence survived the audio+translation filter. That covers both "all local
search tiers empty" and "tiers matched but none had audio+translation". In this
foundation pass we only set the `needs_fallback` marker; no LLM is called.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Candidate, SearchResult

STATUS_OK = "ok"
STATUS_NEEDS_FALLBACK = "needs_fallback"


@dataclass
class Selection:
    status: str  # STATUS_OK | STATUS_NEEDS_FALLBACK
    chosen: Candidate | None
    kept: list[Candidate]  # top-N ranked candidates for the review swap list
    tier: str | None  # which search tier produced the hits (None if fallback)

    @property
    def needs_fallback(self) -> bool:
        return self.status == STATUS_NEEDS_FALLBACK


def select(
    result: SearchResult,
    ranked: list[Candidate],
    *,
    candidates_kept: int,
) -> Selection:
    if not ranked:
        return Selection(
            status=STATUS_NEEDS_FALLBACK, chosen=None, kept=[], tier=None
        )
    kept = ranked[: max(1, candidates_kept)]
    return Selection(
        status=STATUS_OK, chosen=kept[0], kept=kept, tier=result.tier
    )
