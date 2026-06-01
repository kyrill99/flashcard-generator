"""Plain data records passed between the DB layer and the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """Output of the D9 tiered cascade for one word."""

    word: str
    tier: str | None  # "fts_exact" | "like_substring" | "stem" | None (no hits)
    sentence_ids: list[int]


@dataclass
class Candidate:
    """One unique Spanish sentence with audio + a base-language translation.

    Exactly one per spa sentence id after the D7a fan-out collapse. `translation`
    is the shortest base-language translation (the display value); the full set
    of alternates is carried separately in `candidates_json` at enqueue time.
    """

    sentence_id: int
    spa_text: str
    translation: str
    audio_id: int | None = None
    username: str | None = None
    license: str | None = None
    is_native: bool = False
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "sentence_id": self.sentence_id,
            "spa_text": self.spa_text,
            "translation": self.translation,
            "audio_id": self.audio_id,
            "username": self.username,
            "license": self.license,
            "is_native": self.is_native,
            "score": round(self.score, 3),
        }


@dataclass
class CardFields:
    """The seven fields of the custom note type (D3)."""

    word: str
    sentence: str
    sentence_blanked: str
    translation: str
    audio: str  # Anki sound tag, e.g. "[sound:tatoeba_spa_123.mp3]" ("" for none)
    source: str
    flag: str = ""  # "" or "fallback"

    def as_dict(self) -> dict:
        return {
            "Word": self.word,
            "Sentence": self.sentence,
            "SentenceBlanked": self.sentence_blanked,
            "Translation": self.translation,
            "Audio": self.audio,
            "Source": self.source,
            "Flag": self.flag,
        }
