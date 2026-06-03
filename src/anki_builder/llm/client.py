"""OpenAI-SDK chat client: fallback sentence generation + vision word extraction.

`_chat` is the *only* LLM wire seam (mirroring `AnkiClient.invoke`), so tests
monkeypatch it to run without a key or the network. `openai` is imported lazily
inside `_chat` so importing this module stays cheap and offline. The OpenAI SDK's
own retry (`max_retries`) covers HTTP 429 / 5xx with backoff — no extra dep.

The configured model must be vision-capable (D4) since the same client serves
both `generate_sentence` (v1 fallback) and `extract_words` (v1.1 image input).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import LLMConfig

# Built-in SDK retry budget for 429/5xx + connection errors (consideration #1).
_MAX_RETRIES = 3


class FallbackError(RuntimeError):
    """The LLM call failed or returned an unusable / wrong-schema response."""


@dataclass
class FallbackSentence:
    """A generated target-language example: sentence, its translation, L1 gloss."""

    spa_text: str
    translation: str
    gloss: str


class LLMClient:
    """Thin OpenAI chat client. One network seam: :meth:`_chat`."""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def _chat(self, messages: list[dict], *, max_tokens: int) -> str:
        """POST one JSON-mode chat completion; return the message content.

        The single LLM wire seam. Builds the client lazily so a missing/optional
        `openai` install or key never breaks import; relies on the SDK's
        `max_retries` for 429/5xx backoff.
        """
        from openai import OpenAI

        client = OpenAI(
            api_key=self.cfg.api_key,
            base_url=self.cfg.base_url,
            max_retries=_MAX_RETRIES,
        )
        resp = client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    def _parse(self, raw: str) -> dict:
        """Parse the model's JSON content, raising a clean error on garbage."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FallbackError(f"LLM returned non-JSON content: {raw!r}") from exc
        if not isinstance(data, dict):
            raise FallbackError(f"LLM returned a non-object JSON: {data!r}")
        return data

    def generate_sentence(
        self, word: str, *, target_lang: str = "spa", base_lang: str = "eng"
    ) -> FallbackSentence:
        """Generate a short, simple target-language sentence using `word`.

        Returns its base-language translation and a short L1 word gloss too.
        JSON mode guarantees valid JSON but not the schema, so missing keys raise
        `FallbackError` (consideration #2) — the caller falls through to the
        marked-only `needs_fallback` path.
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You write example sentences for a language-learning flashcard "
                    "tool. Reply ONLY with a JSON object."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target language code: {target_lang}. Base language code: "
                    f"{base_lang}. Write ONE short, simple, natural sentence in the "
                    f"target language that uses the word \"{word}\". Return JSON with "
                    'keys: "sentence" (the target-language sentence), "translation" '
                    "(its base-language translation), and \"gloss\" (a 1-4 word "
                    f"base-language gloss of \"{word}\" itself)."
                ),
            },
        ]
        data = self._parse(self._chat(messages, max_tokens=self.cfg.max_tokens))
        try:
            spa_text = str(data["sentence"]).strip()
            translation = str(data["translation"]).strip()
            gloss = str(data.get("gloss", "")).strip()
        except (KeyError, TypeError) as exc:
            raise FallbackError(f"LLM JSON missing expected keys: {data!r}") from exc
        if not spa_text:
            raise FallbackError(f"LLM returned an empty sentence: {data!r}")
        return FallbackSentence(spa_text=spa_text, translation=translation, gloss=gloss)

    def generate_gloss(
        self, word: str, *, target_lang: str = "spa", base_lang: str = "eng"
    ) -> str:
        """A short base-language gloss for a word the offline dictionary lacks.

        Used as a fallback when FreeDict has no entry (its coverage is small). Asks
        for the dictionary form (1-4 words). Off-schema responses raise
        `FallbackError`; the caller treats that as "no gloss" (never fatal).
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise bilingual dictionary. Reply ONLY with a JSON "
                    "object."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target language code: {target_lang}. Base language code: "
                    f"{base_lang}. Give a short base-language gloss (1-4 words, "
                    f"dictionary form, e.g. a verb as \"to add\") of the "
                    f"target-language word \"{word}\". Return JSON with a single key "
                    '"gloss".'
                ),
            },
        ]
        data = self._parse(self._chat(messages, max_tokens=self.cfg.max_tokens))
        try:
            return str(data["gloss"]).strip()
        except (KeyError, TypeError) as exc:
            raise FallbackError(f"gloss JSON missing 'gloss' key: {data!r}") from exc

    def extract_words(self, image_b64: str, mime: str) -> list[str]:
        """Vision: return target-language lemmas found in an image.

        Same `chat.completions` shape with an `image_url` data-URL part. Prompts
        for `{"words": [...]}` (only marked/underlined words if any are marked).
        Parses defensively and truncates to `cfg.max_words` (consideration #3).
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract vocabulary words from an image for a language "
                    "learner. Reply ONLY with a JSON object."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "List the distinct vocabulary words in this image as "
                            "dictionary lemmas. If any words are highlighted, "
                            "underlined, or circled, return ONLY those. Return JSON "
                            'with a single key "words" whose value is an array of '
                            "strings."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            },
        ]
        data = self._parse(self._chat(messages, max_tokens=self.cfg.max_tokens))
        try:
            raw_words = data["words"]
        except (KeyError, TypeError) as exc:
            raise FallbackError(f"vision JSON missing 'words' key: {data!r}") from exc
        if not isinstance(raw_words, list):
            raise FallbackError(f"vision 'words' is not a list: {raw_words!r}")
        words = [str(w).strip() for w in raw_words if str(w).strip()]
        return words[: max(0, self.cfg.max_words)]
