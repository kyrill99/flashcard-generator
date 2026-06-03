"""OpenAI TTS for fallback audio (D4), swappable behind a thin interface.

`_speech` is the *only* TTS wire seam, so tests monkeypatch it to write a canned
mp3 without a key or the network. `openai` is imported lazily inside it. The key
and base_url are reused from the LLM config (D4): one provider, one credential.
"""

from __future__ import annotations

from pathlib import Path

from ..config import TTSConfig

# Built-in SDK retry budget for 429/5xx + connection errors (consideration #1).
_MAX_RETRIES = 3


class TTSClient:
    """Thin OpenAI speech client. One network seam: :meth:`_speech`."""

    def __init__(self, cfg: TTSConfig, *, api_key: str | None, base_url: str):
        self.cfg = cfg
        self.api_key = api_key
        self.base_url = base_url

    def _speech(self, text: str) -> bytes:
        """Synthesize `text` to mp3 bytes. The single TTS wire seam."""
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, max_retries=_MAX_RETRIES
        )
        resp = client.audio.speech.create(
            model=self.cfg.model,
            voice=self.cfg.voice,
            input=text,
            response_format="mp3",
        )
        return resp.read()

    def synthesize_to(self, text: str, dest: str | Path) -> Path:
        """Write the mp3 atomically (`.part` → replace), mirroring audio._stream_to."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = self._speech(text)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest
