"""mp3 download/cache (Step 5): CDN primary, audio_id fallback, cache hit, force.

Uses an httpx MockTransport so no real network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from anki_builder.tatoeba import audio

CDN = "https://audio.tatoeba.org/sentences/spa/{}.mp3"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_caches_from_cdn(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"MP3")

    path = audio.download_audio(731546, lang="spa", cache_dir=tmp_path, client=_client(handler))

    assert path == audio.cached_path(731546, "spa", tmp_path)
    assert path.read_bytes() == b"MP3"
    assert calls == [CDN.format(731546)]


def test_404_falls_back_to_audio_id(tmp_path):
    def handler(request):
        if "audio.tatoeba.org" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"FALLBACK")

    path = audio.download_audio(
        5, lang="spa", cache_dir=tmp_path, audio_id=900, client=_client(handler)
    )
    assert path is not None
    assert path.read_bytes() == b"FALLBACK"


def test_404_without_audio_id_returns_none(tmp_path):
    path = audio.download_audio(
        7, lang="spa", cache_dir=tmp_path, client=_client(lambda r: httpx.Response(404))
    )
    assert path is None


def test_cache_hit_makes_no_request(tmp_path):
    dest = audio.cached_path(1, "spa", tmp_path)
    dest.write_bytes(b"CACHED")

    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("network hit on cache hit")

    path = audio.download_audio(1, lang="spa", cache_dir=tmp_path, client=_client(handler))
    assert path.read_bytes() == b"CACHED"


def test_force_redownloads(tmp_path):
    dest = audio.cached_path(2, "spa", tmp_path)
    dest.write_bytes(b"OLD")

    path = audio.download_audio(
        2, lang="spa", cache_dir=tmp_path, force=True,
        client=_client(lambda r: httpx.Response(200, content=b"NEW")),
    )
    assert path.read_bytes() == b"NEW"
