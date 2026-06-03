"""v1.1 vision (step 8): extract_words parsing/truncation and _load_words --image.

The LLM wire seam (`LLMClient._chat`) is monkeypatched, so no key/network is used.
"""

from __future__ import annotations

import argparse

import pytest

from anki_builder import cli
from anki_builder.config import Config, LLMConfig
from anki_builder.llm import vision
from anki_builder.llm.client import FallbackError, LLMClient


def _img(tmp_path, suffix=".jpg"):
    p = tmp_path / f"photo{suffix}"
    p.write_bytes(b"\xff\xd8\xff\xe0fake-image-bytes")
    return p


def test_extract_words_parses(monkeypatch, tmp_path):
    cfg = Config(llm=LLMConfig(api_key="x", max_words=50))
    monkeypatch.setattr(
        LLMClient, "_chat", lambda self, messages, *, max_tokens: '{"words": ["comer", "gato"]}'
    )
    assert vision.extract_words(_img(tmp_path), cfg=cfg) == ["comer", "gato"]


def test_extract_words_truncates_to_max(monkeypatch, tmp_path):
    cfg = Config(llm=LLMConfig(api_key="x", max_words=2))
    monkeypatch.setattr(
        LLMClient, "_chat", lambda self, messages, *, max_tokens: '{"words": ["a","b","c","d"]}'
    )
    assert vision.extract_words(_img(tmp_path), cfg=cfg) == ["a", "b"]


def test_extract_words_bad_schema_raises(monkeypatch, tmp_path):
    cfg = Config(llm=LLMConfig(api_key="x"))
    monkeypatch.setattr(
        LLMClient, "_chat", lambda self, messages, *, max_tokens: '{"oops": []}'
    )
    with pytest.raises(FallbackError):
        vision.extract_words(_img(tmp_path), cfg=cfg)


def test_extract_words_missing_file_raises(tmp_path):
    cfg = Config(llm=LLMConfig(api_key="x"))
    with pytest.raises(FileNotFoundError):
        vision.extract_words(tmp_path / "nope.png", cfg=cfg)


def test_load_words_image_path(monkeypatch, tmp_path):
    cfg = Config()
    img = _img(tmp_path)
    monkeypatch.setattr(vision, "extract_words", lambda path, *, cfg: ["uno", "dos"])
    args = argparse.Namespace(word=None, words=None, image=str(img))
    assert cli._load_words(args, cfg) == ["uno", "dos"]
