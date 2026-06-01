"""Tatoeba audio URL + media filename helpers, plus mp3 download/cache (D8/D10).

The URL/filename builders are pure functions (no network); `download_audio`
fetches the native-speaker clip into the local media cache on demand, keyed by
the deterministic filename so a second call is a cache hit. Push reads the cached
file and hands it to AnkiConnect's `storeMediaFile`.
"""

from __future__ import annotations

from pathlib import Path

import httpx

# D8 — primary CDN form, keyed by sentence_id.
_CDN_URL = "https://audio.tatoeba.org/sentences/{lang}/{sentence_id}.mp3"
# Fallback endpoint, keyed by audio_id when the CDN form 404s.
_DOWNLOAD_URL = "https://tatoeba.org/audio/download/{audio_id}"

_CHUNK = 1 << 16


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


def cached_path(sentence_id: int, lang: str, cache_dir: str | Path) -> Path:
    """Where the mp3 for this sentence lives (or would live) in the cache."""
    return Path(cache_dir) / media_filename(sentence_id, lang)


def _stream_to(url: str, dest: Path, client: httpx.Client) -> None:
    """Stream a GET to `dest` via a `.part` temp + atomic replace.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response so the caller can
    distinguish a 404 (try the fallback endpoint) from success.
    """
    tmp = dest.with_name(dest.name + ".part")
    with client.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as out:
            for chunk in resp.iter_bytes(_CHUNK):
                out.write(chunk)
    tmp.replace(dest)


def download_audio(
    sentence_id: int,
    *,
    lang: str = "spa",
    cache_dir: str | Path,
    audio_id: int | None = None,
    force: bool = False,
    client: httpx.Client | None = None,
) -> Path | None:
    """Download + cache the native-speaker mp3; return its path (None on failure).

    Cache hit (and not ``force``) short-circuits with no request. Otherwise tries
    the D8 CDN URL first; on a 404 with a known ``audio_id`` it falls back to the
    ``/audio/download/<audio_id>`` endpoint. Returns ``None`` (rather than
    raising) when no source yields the file, so the caller can skip/flag the row.
    """
    dest = cached_path(sentence_id, lang, cache_dir)
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    owns_client = client is None
    client = client or httpx.Client()
    try:
        try:
            _stream_to(audio_url(sentence_id, lang), dest, client)
            return dest
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and audio_id is not None:
                try:
                    _stream_to(audio_fallback_url(audio_id), dest, client)
                    return dest
                except httpx.HTTPError:
                    return None
            return None
        except httpx.HTTPError:
            return None
    finally:
        if owns_client:
            client.close()
