# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal CLI that mines Spanish Anki cards from the **Tatoeba** corpus: given a target word, it finds a real native-audio example sentence + its English translation and builds card fields, staging each into a `review_queue` for mandatory human review before any deck import. The LLM is only a fallback when Tatoeba has no usable match.

The repo dir is `flashcard-generator`, but the Python package is `anki_builder` and the console script is `anki-builder`.

**Status — read before assuming a feature exists.** Only the *foundation pass* is built: config, the SQLite corpus DB, the tiered search→filter→rank→select pipeline, card-field construction, and enqueue. **Not built:** AnkiConnect push, the review web app, and the live LLM/TTS fallback — `review` and `push` are CLI stubs, and `select` only *marks* `needs_fallback` without calling an LLM. See [docs/status/implementation-status.md](docs/status/implementation-status.md) for the build-step map and what's next.

## Commands

```bash
uv sync                          # create .venv + install deps (uses uv.lock)
uv run pytest                    # full suite (~23 tests, no network/Anki/API key needed)
uv run pytest -v                 # per-test names
uv run pytest tests/test_search.py::test_like_catches_enclitic   # a single case

uv run anki-builder fetch-dumps  # download Tatoeba dumps (network) → data/dumps/
uv run anki-builder build-db     # one-time ingest dumps → data/tatoeba.db
uv run anki-builder run --word comer            # mine one word → review_queue
uv run anki-builder run --word comer --dry-run  # print only, write nothing
uv run anki-builder run --words file.txt        # one word per line; '#' comments ok
```

There is no linter/formatter configured. `pytest` is the only gate.

## Architecture

Per-word flow (each stage is its own module; tags like **D9** refer to decisions in [docs/specs/implementation_plan_v1.md](docs/specs/implementation_plan_v1.md)):

```
word → search (tiered cascade)  db/queries.py search()   [D9]
     → filter + fan-out collapse db/queries.py filtered_candidates()  [D7a]
     → rank (short/simple first, native boost)  pipeline/rank.py  [D13]
     → select #1 or mark needs_fallback  pipeline/select.py  [D4 seam]
     → build 7 card fields incl. SentenceBlanked  pipeline/cards.py  [D3]
     → enqueue row  db/queries.py enqueue()  [D11]
```

`cli.py::cmd_run` orchestrates this; the `pipeline/` modules are thin and stateless, with `db/queries.py` holding all the SQL.

**All per-word work is local SQL — the Tatoeba API is never hit at runtime (D5).** The corpus is built once by `build-db` from dumps; the network is only touched by `fetch-dumps`.

### The pieces that need cross-file context to understand

- **The three search tiers are *not* uniform on accents, by design** ([db/queries.py](src/anki_builder/db/queries.py) + [stemming.py](src/anki_builder/stemming.py)). Tier 1 (`fts_exact`) and tier 3 (`stem`) query `sentences_fts`, which keeps accents (`remove_diacritics 0`), so `como` ≠ `cómodo`. Tier 2 (`like_substring`) queries the separate `text_fold` column — lowercased, vowel-accents stripped, **but ñ deliberately kept** (`año` ≠ `ano`) — so `come` matches inside the enclitic `cómelo`. The cascade returns the *first* non-empty tier.

- **`stemming.py` is shared between ingest and query on purpose.** `build.py` writes `stems_blob(text)` into the FTS `stems` column; `queries.py` stems the query word with the same functions. If you change tokenisation/stemming/folding, indexed stems and query stems must stay identical or the stem tier silently stops matching — and you must rebuild the DB.

- **`SentenceBlanked` mirrors the search tiers** ([cards.py](src/anki_builder/pipeline/cards.py) `_match_priority`): it blanks the target token using the same exact > accent-fold > folded-substring > stem priority, and blanks *all* tokens at the strongest matched level so a repeated word never leaves the answer visible.

- **Fan-out collapse (D7a):** one Spanish sentence with N English translations must yield exactly **one** candidate, not N. `filtered_candidates()` does this with a scalar subquery picking the shortest translation as the display value; alternates are kept in `candidates_json` for the future review swap UI. It also requires *both* audio AND a translation — that filter is why a word can hit a tier yet still route to `needs_fallback`.

- **Ingest is ordered to keep the DB small** ([db/build.py](src/anki_builder/db/build.py)): load target (`spa`) sentences → load the bilingual `spa-eng` links → load **only** the `eng` sentences those links reference. `build-db` rebuilds the corpus tables but **preserves `review_queue`**. `user_languages` is optional — absent it, the native-audio ranking boost simply disables (every candidate non-native).

- **Audio is URL/filename-only right now** ([tatoeba/audio.py](src/anki_builder/tatoeba/audio.py)): pure functions building the CDN URL and the deterministic `tatoeba_spa_<id>.mp3` name (keyed by sentence id, which is always known). No mp3 is actually downloaded yet.

## Config

`load_config` reads `./config.toml` if present (override with `--config` or `ANKI_BUILDER_CONFIG`), else falls back to dataclass defaults — so the tool and tests run with no config file. Copy `config.example.toml` → `config.toml` and `.env.example` → `.env` to override. **`config.toml`, `.env`, and `data/` are gitignored**; secrets (`OPENAI_API_KEY`) come only from the environment, never the TOML. `[anki]`, `[llm]`, and `[tts]` sections are parsed but unused until the later pass — keep them stable.

## Conventions

- **Decision tags `D1`–`D13`** appear throughout docstrings; they trace back to [docs/specs/implementation_plan_v1.md](docs/specs/implementation_plan_v1.md). When changing pipeline behaviour, keep the tag/comment in sync.
- **The fixture corpus in [tests/conftest.py](tests/conftest.py) is the behavioural contract.** Each Spanish sentence is hand-picked to exercise one behaviour (accent distinction, enclitic, fan-out, audio/translation filter, ranking, native tie-break). If a behaviour change is intentional, update the fixture and the affected test in the same edit. Re-run `pytest` after touching the schema, `stemming.py`, search SQL, ranking weights, or blanking logic.
- Inter-stage data are plain dataclasses in [models.py](src/anki_builder/models.py) (`SearchResult`, `Candidate`, `CardFields`); `as_dict()` methods define the JSON shape stored in `review_queue`.
