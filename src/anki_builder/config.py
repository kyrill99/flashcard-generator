"""Load configuration from a TOML file plus environment (D1/D4/D6).

Only a subset of the config is consumed in this foundation pass; the rest is
parsed-but-unused so the schema is stable for the later Anki/LLM integration.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.toml"


@dataclass(frozen=True)
class LanguageConfig:
    target_lang: str = "spa"
    base_lang: str = "eng"


@dataclass(frozen=True)
class PathsConfig:
    db_path: Path = Path("data/tatoeba.db")
    dumps_dir: Path = Path("data/dumps")
    media_cache: Path = Path("data/media")


@dataclass(frozen=True)
class RankingConfig:
    length_weight: float = 1.0
    word_count_weight: float = 2.0
    native_audio_boost: float = 50.0
    ideal_min_words: int = 4
    ideal_max_words: int = 12
    candidates_kept: int = 8


@dataclass(frozen=True)
class AnkiConfig:
    deck: str = "Spanish::Mining"
    note_type: str = "AnkiBuilder Spanish"
    connect_url: str = "http://127.0.0.1:8765"


@dataclass(frozen=True)
class LLMConfig:
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    fallback_enabled: bool = True
    api_key: str | None = None
    max_tokens: int = 400  # cap per chat completion (cost control)
    max_words: int = 50  # cap on words extracted from one image (cost control)


@dataclass(frozen=True)
class TTSConfig:
    voice: str = "alloy"
    model: str = "gpt-4o-mini-tts"


@dataclass(frozen=True)
class Config:
    languages: LanguageConfig = field(default_factory=LanguageConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    anki: AnkiConfig = field(default_factory=AnkiConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    source_path: Path | None = None


def _coerce_paths(raw: dict, *, base: Path) -> PathsConfig:
    defaults = PathsConfig()

    def resolve(value: str | None, fallback: Path) -> Path:
        p = Path(value) if value else fallback
        return p if p.is_absolute() else (base / p)

    return PathsConfig(
        db_path=resolve(raw.get("db_path"), defaults.db_path),
        dumps_dir=resolve(raw.get("dumps_dir"), defaults.dumps_dir),
        media_cache=resolve(raw.get("media_cache"), defaults.media_cache),
    )


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Read ``config.toml`` (if present) and overlay env-supplied secrets.

    Missing file or missing keys fall back to dataclass defaults, so the tool
    runs out-of-the-box for unit tests and dry runs. Relative paths in
    ``[paths]`` are resolved against the config file's directory (or CWD when
    no file exists).

    ``.env`` (CWD or a parent) is loaded best-effort so ``OPENAI_API_KEY`` is
    honoured for the LLM/TTS fallback without exporting it manually. The TOML
    never carries secrets (D4) — the key comes only from the environment.
    """
    try:  # best-effort: .env is optional, and python-dotenv may be absent
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover — dotenv is a declared dep
        pass

    if path is not None:
        config_path = Path(path)
    else:
        env_path = os.environ.get("ANKI_BUILDER_CONFIG")
        config_path = Path(env_path) if env_path else Path(DEFAULT_CONFIG_NAME)

    raw: dict = {}
    source_path: Path | None = None
    if config_path.exists():
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
        source_path = config_path.resolve()
        base = config_path.resolve().parent
    else:
        base = Path.cwd()

    languages_raw = raw.get("languages", {})
    ranking_raw = raw.get("ranking", {})
    anki_raw = raw.get("anki", {})
    llm_raw = raw.get("llm", {})
    tts_raw = raw.get("tts", {})

    languages = LanguageConfig(
        target_lang=languages_raw.get("target_lang", LanguageConfig.target_lang),
        base_lang=languages_raw.get("base_lang", LanguageConfig.base_lang),
    )

    ranking_defaults = RankingConfig()
    ranking = RankingConfig(
        length_weight=float(ranking_raw.get("length_weight", ranking_defaults.length_weight)),
        word_count_weight=float(
            ranking_raw.get("word_count_weight", ranking_defaults.word_count_weight)
        ),
        native_audio_boost=float(
            ranking_raw.get("native_audio_boost", ranking_defaults.native_audio_boost)
        ),
        ideal_min_words=int(ranking_raw.get("ideal_min_words", ranking_defaults.ideal_min_words)),
        ideal_max_words=int(ranking_raw.get("ideal_max_words", ranking_defaults.ideal_max_words)),
        candidates_kept=int(ranking_raw.get("candidates_kept", ranking_defaults.candidates_kept)),
    )

    anki_defaults = AnkiConfig()
    anki = AnkiConfig(
        deck=anki_raw.get("deck", anki_defaults.deck),
        note_type=anki_raw.get("note_type", anki_defaults.note_type),
        connect_url=anki_raw.get("connect_url", anki_defaults.connect_url),
    )

    llm_defaults = LLMConfig()
    llm = LLMConfig(
        model=llm_raw.get("model", llm_defaults.model),
        base_url=llm_raw.get("base_url", llm_defaults.base_url),
        fallback_enabled=bool(llm_raw.get("fallback_enabled", llm_defaults.fallback_enabled)),
        api_key=os.environ.get("OPENAI_API_KEY"),
        max_tokens=int(llm_raw.get("max_tokens", llm_defaults.max_tokens)),
        max_words=int(llm_raw.get("max_words", llm_defaults.max_words)),
    )

    tts_defaults = TTSConfig()
    tts = TTSConfig(
        voice=tts_raw.get("voice", tts_defaults.voice),
        model=tts_raw.get("model", tts_defaults.model),
    )

    return Config(
        languages=languages,
        paths=_coerce_paths(raw.get("paths", {}), base=base),
        ranking=ranking,
        anki=anki,
        llm=llm,
        tts=tts,
        source_path=source_path,
    )
