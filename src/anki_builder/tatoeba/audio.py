"""Tatoeba audio URL + media filename helpers (D8/D10).

Pure functions only in this pass: build the fixed-form CDN URL (keyed by
sentence id, which we always have) and the deterministic media filename used for
idempotent dedupe at push time. The actual mp3 download/cache is deferred to the
later integration pass.
"""

from __future__ import annotations

# D8 — primary CDN form, keyed by sentence_id.
_CDN_URL = "https://audio.tatoeba.org/sentences/{lang}/{sentence_id}.mp3"
# Fallback endpoint, keyed by audio_id when the CDN form 404s.
_DOWNLOAD_URL = "https://tatoeba.org/audio/download/{audio_id}"


def audio_url(sentence_id: int, lang: str = "spa") -> str:
    return _CDN_URL.format(lang=lang, sentence_id=sentence_id)


def audio_fallback_url(audio_id: int) -> str:
    return _DOWNLOAD_URL.format(audio_id=audio_id)


def media_filename(sentence_id: int, lang: str = "spa") -> str:
    """Deterministic filename for storeMediaFile dedupe (D10)."""
    return f"tatoeba_{lang}_{sentence_id}.mp3"


def sound_tag(sentence_id: int, lang: str = "spa") -> str:
    """Anki audio field value referencing the (to-be-stored) media file."""
    return f"[sound:{media_filename(sentence_id, lang)}]"
