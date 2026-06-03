"""Build the eight note-type fields (D3), including SentenceBlanked.

`SentenceBlanked` blanks the target word's surface token(s) in the chosen
sentence, accent-/case-/punctuation-insensitively. Matching mirrors the D9 tiers
so whichever form the sentence actually uses (exact, accented, enclitic, or an
inflection) is the one that gets blanked. All tokens at the strongest match
level are blanked, so a word appearing twice never leaves the answer visible.
"""

from __future__ import annotations

import re

from ..models import Candidate, CardFields
from ..stemming import fold_accents, stem_word
from ..tatoeba.audio import sound_tag

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
BLANK = "____"


def _match_priority(token: str, word: str) -> int | None:
    """Lower = stronger match. None = this token is not the target word.

    0 exact · 1 accent-fold equal · 2 folded word inside token (enclitic) ·
    3 same Snowball stem (inflection).
    """
    t = token.lower()
    w = word.lower()
    if t == w:
        return 0
    tf, wf = fold_accents(token), fold_accents(word)
    if tf == wf:
        return 1
    if wf and wf in tf:
        return 2
    if stem_word(token) == stem_word(word):
        return 3
    return None


def blank_sentence(text: str, word: str, blank: str = BLANK) -> str:
    """Replace the target word's strongest-matching token(s) with `blank`."""
    spans = [
        (m.start(), m.end(), _match_priority(m.group(0), word))
        for m in _WORD_RE.finditer(text)
    ]
    priorities = [p for (_s, _e, p) in spans if p is not None]
    if not priorities:
        return text  # no token matched (rare; selected sentences usually do)

    best = min(priorities)
    out: list[str] = []
    last = 0
    for start, end, priority in spans:
        if priority == best:
            out.append(text[last:start])
            out.append(blank)
            last = end
    out.append(text[last:])
    return "".join(out)


def build_source(candidate: Candidate) -> str:
    src = f"Tatoeba #{candidate.sentence_id}"
    if candidate.username:
        src += f" · {candidate.username}"
    return src


def build_card_fields(
    word: str,
    candidate: Candidate,
    *,
    target_lang: str = "spa",
    word_translation: str = "",
    flag: str = "",
) -> CardFields:
    """Assemble CardFields from a chosen Tatoeba candidate (D3).

    `word_translation` is the L1 word gloss (from `queries.gloss_for`); it defaults
    to "" so a missing gloss degrades to a one-field human edit in review.
    """
    return CardFields(
        word=word,
        word_translation=word_translation,
        sentence=candidate.spa_text,
        sentence_blanked=blank_sentence(candidate.spa_text, word),
        translation=candidate.translation,
        audio=sound_tag(candidate.sentence_id, target_lang),
        source=build_source(candidate),
        flag=flag,
    )


def build_fallback_fields(
    word: str,
    spa_text: str,
    translation: str,
    *,
    word_translation: str = "",
    audio_filename: str | None = None,
) -> CardFields:
    """Assemble CardFields for an LLM-generated sentence (D4 fallback).

    A synthetic Candidate can't reuse `build_card_fields` because `sound_tag`
    keys the audio off a real Tatoeba `sentence_id`; a fallback card's audio is a
    TTS file named independently. Keeping this here makes cards.py the single
    place the eight fields are assembled. `flag="fallback"` renders the review
    badge; `audio` is empty when TTS produced no file (card pushed silent).
    """
    return CardFields(
        word=word,
        word_translation=word_translation,
        sentence=spa_text,
        sentence_blanked=blank_sentence(spa_text, word),
        translation=translation,
        audio=f"[sound:{audio_filename}]" if audio_filename else "",
        source="LLM fallback",
        flag="fallback",
    )
