"""LLM/TTS fallback (step 7): sentence parsing, TTS write, generate_fallback,
and cmd_run's needs_fallback branch. All mocked — no key, no network.

The two wire seams (`LLMClient._chat`, `TTSClient._speech`) are monkeypatched, so
nothing here touches OpenAI; `cmd_run` is driven with a hand-built Namespace.
"""

from __future__ import annotations

import argparse
import dataclasses

import pytest

from anki_builder import cli, db
from anki_builder.config import Config, LLMConfig, PathsConfig, TTSConfig
from anki_builder.db import queries
from anki_builder.llm.client import FallbackError, LLMClient
from anki_builder.llm.tts import TTSClient
from anki_builder.pipeline import cards, fallback
from anki_builder.pipeline import gloss as gloss_mod

from conftest import seed_corpus  # shared in-memory corpus seeder


def _llm(monkeypatch, content: str) -> LLMClient:
    """An LLMClient whose only wire seam returns canned JSON `content`."""
    client = LLMClient(LLMConfig(api_key="x"))
    monkeypatch.setattr(client, "_chat", lambda messages, *, max_tokens: content)
    return client


# --- LLMClient.generate_sentence -------------------------------------------


def test_generate_sentence_parses(monkeypatch):
    llm = _llm(
        monkeypatch,
        '{"sentence": "Quiero comer.", "translation": "I want to eat.", "gloss": "to eat"}',
    )
    fb = llm.generate_sentence("comer", target_lang="spa", base_lang="eng")
    assert fb.spa_text == "Quiero comer."
    assert fb.translation == "I want to eat."
    assert fb.gloss == "to eat"


def test_generate_sentence_missing_key_raises(monkeypatch):
    # JSON mode guarantees valid JSON, not the schema (consideration #2).
    llm = _llm(monkeypatch, '{"translation": "x"}')  # no "sentence"
    with pytest.raises(FallbackError):
        llm.generate_sentence("comer")


def test_generate_sentence_non_json_raises(monkeypatch):
    llm = _llm(monkeypatch, "not json at all")
    with pytest.raises(FallbackError):
        llm.generate_sentence("comer")


# --- LLMClient.generate_gloss + resolve_gloss ------------------------------


def test_generate_gloss_parses(monkeypatch):
    llm = _llm(monkeypatch, '{"gloss": "to add"}')
    assert llm.generate_gloss("añadir", target_lang="spa", base_lang="eng") == "to add"


def test_generate_gloss_missing_key_raises(monkeypatch):
    llm = _llm(monkeypatch, '{"definition": "to add"}')  # wrong key
    with pytest.raises(FallbackError):
        llm.generate_gloss("añadir")


def test_resolve_gloss_prefers_dict_no_llm_call(corpus, monkeypatch):
    # A dictionary hit must never reach the LLM.
    llm = LLMClient(LLMConfig(api_key="x"))
    monkeypatch.setattr(
        llm, "generate_gloss",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called on a dict hit")),
    )
    assert gloss_mod.resolve_gloss(corpus, "gato", llm_client=llm) == ("cat", "dict")


def test_resolve_gloss_llm_fills_dict_miss(corpus, monkeypatch):
    # `perro` is absent from the fixture glossary → the LLM supplies the gloss.
    llm = LLMClient(LLMConfig(api_key="x"))
    monkeypatch.setattr(llm, "generate_gloss", lambda word, *, target_lang, base_lang: "dog")
    assert gloss_mod.resolve_gloss(corpus, "perro", llm_client=llm) == ("dog", "llm")


def test_resolve_gloss_llm_failure_is_blank(corpus, monkeypatch):
    llm = LLMClient(LLMConfig(api_key="x"))

    def boom(word, *, target_lang, base_lang):
        raise FallbackError("nope")

    monkeypatch.setattr(llm, "generate_gloss", boom)
    assert gloss_mod.resolve_gloss(corpus, "perro", llm_client=llm) == ("", "")


def test_resolve_gloss_no_client_dict_miss(corpus):
    assert gloss_mod.resolve_gloss(corpus, "perro") == ("", "")


# --- TTSClient.synthesize_to -----------------------------------------------


def test_synthesize_to_writes_mp3_atomically(monkeypatch, tmp_path):
    tts = TTSClient(TTSConfig(), api_key="x", base_url="http://x")
    monkeypatch.setattr(tts, "_speech", lambda text: b"MP3DATA")
    dest = tmp_path / "media" / "clip.mp3"  # parent does not exist yet
    out = tts.synthesize_to("Hola", dest)
    assert out == dest
    assert dest.read_bytes() == b"MP3DATA"
    assert not dest.with_name(dest.name + ".part").exists()


# --- pipeline.fallback.generate_fallback -----------------------------------


def _cfg(tmp_path) -> Config:
    return Config(
        paths=PathsConfig(
            db_path=tmp_path / "db", dumps_dir=tmp_path / "d", media_cache=tmp_path / "media"
        ),
        llm=LLMConfig(api_key="x"),
    )


def test_generate_fallback_builds_flagged_card(corpus, monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    llm = _llm(
        monkeypatch,
        '{"sentence": "Quiero comer.", "translation": "I want to eat.", "gloss": "to eat"}',
    )
    tts = TTSClient(cfg.tts, api_key="x", base_url=cfg.llm.base_url)
    monkeypatch.setattr(tts, "_speech", lambda text: b"MP3")

    result = fallback.generate_fallback(
        "comer", cfg=cfg, conn=corpus, llm_client=llm, tts_client=tts
    )

    assert result.fields.flag == "fallback"
    assert result.fields.source == "LLM fallback"
    assert result.fields.sentence_blanked == "Quiero ____."  # blanks the word
    assert result.fields.word_translation == "to eat"  # prefers the LLM gloss
    assert result.audio_filename and result.audio_filename.startswith("fallback_spa_comer_")
    assert (cfg.paths.media_cache / result.audio_filename).read_bytes() == b"MP3"


def test_generate_fallback_uses_dict_gloss_when_llm_blank(corpus, monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    llm = _llm(
        monkeypatch,
        '{"sentence": "Tengo un gato.", "translation": "I have a cat.", "gloss": ""}',
    )
    tts = TTSClient(cfg.tts, api_key="x", base_url=cfg.llm.base_url)
    monkeypatch.setattr(tts, "_speech", lambda text: b"MP3")

    result = fallback.generate_fallback(
        "gato", cfg=cfg, conn=corpus, llm_client=llm, tts_client=tts
    )
    assert result.fields.word_translation == "cat"  # gloss_for("gato")


def test_generate_fallback_tts_failure_is_silent(corpus, monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    llm = _llm(monkeypatch, '{"sentence": "Hola.", "translation": "Hi.", "gloss": ""}')
    tts = TTSClient(cfg.tts, api_key="x", base_url=cfg.llm.base_url)

    def boom(text):
        raise RuntimeError("tts unavailable")

    monkeypatch.setattr(tts, "_speech", boom)

    result = fallback.generate_fallback(
        "hola", cfg=cfg, conn=corpus, llm_client=llm, tts_client=tts
    )
    assert result.audio_filename is None  # silent, but still a card
    assert result.fields.audio == ""
    assert result.fields.flag == "fallback"


# --- cmd_run wiring --------------------------------------------------------


def _run_args(**kw) -> argparse.Namespace:
    base = dict(word=None, words=None, image=None, dry_run=False, force=False, no_fallback=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def file_cfg(tmp_path) -> Config:
    """A seeded file-backed corpus + a Config pointing at it."""
    db_path = tmp_path / "tatoeba.db"
    conn = db.connect(db_path)
    seed_corpus(conn)
    conn.close()
    return Config(
        paths=PathsConfig(
            db_path=db_path, dumps_dir=tmp_path / "dumps", media_cache=tmp_path / "media"
        )
    )


def test_cmd_run_fallback_enqueues_pending(file_cfg, monkeypatch):
    fields = cards.build_fallback_fields(
        "zzqwx", "Una zzqwx aquí.", "A zzqwx here.",
        word_translation="g", audio_filename="fallback_spa_zzqwx_x.mp3",
    )
    monkeypatch.setattr(
        fallback,
        "generate_fallback",
        lambda word, *, cfg, conn, llm_client=None: fallback.FallbackResult(
            fields=fields, audio_filename="fallback_spa_zzqwx_x.mp3"
        ),
    )
    cfg = dataclasses.replace(file_cfg, llm=LLMConfig(api_key="x", fallback_enabled=True))

    rc = cli.cmd_run(_run_args(word="zzqwx"), cfg)

    assert rc == 0
    conn = db.connect(cfg.paths.db_path)
    rows = queries.list_queue(conn, status="pending")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["flag"] == "fallback"
    assert rows[0]["fields"]["Flag"] == "fallback"
    assert rows[0]["chosen_sentence_id"] is None


def test_cmd_run_no_key_marks_needs_fallback(file_cfg):
    cfg = dataclasses.replace(file_cfg, llm=LLMConfig(api_key=None, fallback_enabled=True))

    rc = cli.cmd_run(_run_args(word="zzqwx"), cfg)

    assert rc == 0
    conn = db.connect(cfg.paths.db_path)
    rows = queries.list_queue(conn, status="needs_fallback")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["fields"] == {}  # marked only — no fields generated


def test_cmd_run_no_fallback_flag_skips_llm(file_cfg, monkeypatch):
    # Even with a key, --no-fallback forces the marker path (no LLM call).
    def explode(*a, **k):
        raise AssertionError("LLM must not be called with --no-fallback")

    monkeypatch.setattr(fallback, "generate_fallback", explode)
    cfg = dataclasses.replace(file_cfg, llm=LLMConfig(api_key="x", fallback_enabled=True))

    rc = cli.cmd_run(_run_args(word="zzqwx", no_fallback=True), cfg)

    assert rc == 0
    conn = db.connect(cfg.paths.db_path)
    assert len(queries.list_queue(conn, status="needs_fallback")) == 1
    conn.close()


def test_cmd_run_llm_gloss_fills_dict_miss(file_cfg, monkeypatch):
    # `perro` matches Tatoeba sentences 8/9 (OK branch) but is not a FreeDict
    # headword → the LLM gloss fills the WordTranslation field at mining time.
    monkeypatch.setattr(
        LLMClient, "_chat", lambda self, messages, *, max_tokens: '{"gloss": "dog"}'
    )
    cfg = dataclasses.replace(file_cfg, llm=LLMConfig(api_key="x", fallback_enabled=True))

    rc = cli.cmd_run(_run_args(word="perro"), cfg)

    assert rc == 0
    conn = db.connect(cfg.paths.db_path)
    rows = queries.list_queue(conn, status="pending")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["fields"]["Word"] == "perro"
    assert rows[0]["fields"]["WordTranslation"] == "dog"  # from the LLM, not FreeDict


def test_cmd_run_dry_run_does_not_call_llm(file_cfg, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("dry-run must not hit the network")

    monkeypatch.setattr(fallback, "generate_fallback", explode)
    cfg = dataclasses.replace(file_cfg, llm=LLMConfig(api_key="x", fallback_enabled=True))

    rc = cli.cmd_run(_run_args(word="zzqwx", dry_run=True), cfg)

    assert rc == 0
    conn = db.connect(cfg.paths.db_path)
    assert queries.list_queue(conn) == []  # nothing written in dry-run
    conn.close()
