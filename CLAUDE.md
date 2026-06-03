# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal CLI that mines Spanish Anki cards from the **Tatoeba** corpus: given a target word, it finds a real native-audio example sentence + its English translation, looks up a short L1 word gloss from an offline **FreeDict** dictionary, and builds card fields, staging each into a `review_queue` for mandatory human review before any deck import. The LLM is only a fallback when Tatoeba has no usable match.

The note type emits **two cards** from one record: Card 1 — *Contextual Recognition* (`Word` + autoplay sentence audio → gloss + sentence + translation); Card 2 — *Productive Cloze* (the L1 gloss prompt + blanked sentence + `{{type:Word}}`). The **8 fields** are `Word · WordTranslation · Sentence · SentenceBlanked · Translation · Audio · Source · Flag` (see [anki/model.py](src/anki_builder/anki/model.py)). `WordTranslation` is the L1 word gloss (`comer` → *to eat*), distinct from `Translation` (the full sentence translation).

The repo dir is `flashcard-generator`, but the Python package is `anki_builder` and the console script is `anki-builder`.

**Status — v1.1 complete.** Built: config, the SQLite corpus DB, the tiered search→filter→rank→select pipeline, card-field construction, enqueue, **AnkiConnect push** (`anki/`), **audio mp3 download/cache**, the **FastAPI review web app** (`review/`), the **live OpenAI LLM/TTS fallback** (`llm/`, `pipeline/fallback.py` — runs during `run` when no Tatoeba match survives, gated by a key), and **v1.1 image word-extraction** (`run --image` → `llm/vision.py`). The full `run → review → push` loop works against a real Anki. See [docs/status/implementation-status.md](docs/status/implementation-status.md) for the build-step map.

## Commands

```bash
uv sync                          # create .venv + install deps (uses uv.lock)
uv run pytest                    # full suite (~85 tests, no network/Anki/API key needed)
uv run pytest -v                 # per-test names
uv run pytest tests/test_search.py::test_like_catches_enclitic   # a single case

uv run anki-builder fetch-dumps  # download Tatoeba dumps + FreeDict spa-eng (network) → data/dumps/
uv run anki-builder build-db     # one-time ingest dumps + glossary → data/tatoeba.db
uv run anki-builder run --word comer            # mine one word → review_queue
uv run anki-builder run --word comer --dry-run  # print only, write nothing (no LLM call)
uv run anki-builder run --words file.txt        # one word per line; '#' comments ok
uv run anki-builder run --image photo.jpg       # v1.1: extract words from an image → same pipeline
uv run anki-builder run --word comer --force    # re-mine even if already queued (default: skip, prints "skipped (already in queue)")
uv run anki-builder run --word zzqwx --no-fallback  # mark needs_fallback only; never call the LLM (offline)
uv run anki-builder review                      # launch the review web app (D2 gate) → http://127.0.0.1:8000
uv run anki-builder push                        # push accepted rows → Anki (needs Anki + AnkiConnect running)
uv run anki-builder push --dry-run              # print Anki payloads, touch nothing
uv run anki-builder push --force               # allow duplicates (sets allowDuplicate, overrides canAddNotes dedup, D12)
```

There is no linter/formatter configured. `pytest` is the only gate. The suite mocks Anki (AnkiConnect `invoke`) and audio HTTP, so it never needs a live Anki or network.

## Architecture

Per-word flow (each stage is its own module; tags like **D9** refer to decisions in [docs/specs/implementation_plan_v1.md](docs/specs/implementation_plan_v1.md)):

```
(word | --words file | --image photo.jpg → llm/vision.py extract_words [D4])
word → search (tiered cascade)  db/queries.py search()   [D9]
     → filter + fan-out collapse db/queries.py filtered_candidates()  [D7a]
     → rank (short/simple first, native boost)  pipeline/rank.py  [D13]
     → select #1, else needs_fallback  pipeline/select.py  [D4 seam]
        └─ LLM fallback (sentence + TTS, flagged, pending)  pipeline/fallback.py + llm/  [D4]
     → gloss lookup (FreeDict)  db/queries.py gloss_for()
     → build 8 card fields incl. SentenceBlanked + WordTranslation  pipeline/cards.py  [D3]
     → enqueue row (status=pending)  db/queries.py enqueue()  [D11]
     → REVIEW gate (swap/edit/accept/delete)  review/server.py  [D2]   ← status=accepted
     → push (resolve audio, storeMediaFile, canAddNotes, addNote)  anki/push.py  [D10/D12]   ← status=pushed
```

`cli.py::cmd_run` orchestrates the mining stages (including the fallback branch); `cmd_review` launches the web gate and `cmd_push`/the UI run `anki/push.py::push_accepted`. The `pipeline/` modules are thin and stateless, with `db/queries.py` holding all the SQL. Status lifecycle in `review_queue`: `pending → accepted → pushed` (plus `deleted` and the marked-only `needs_fallback` written when fallback is off/keyless).

**All per-word work is local SQL — the Tatoeba API is never hit at runtime (D5).** The corpus is built once by `build-db` from dumps; the network is only touched by `fetch-dumps`.

### The pieces that need cross-file context to understand

- **The three search tiers are *not* uniform on accents, by design** ([db/queries.py](src/anki_builder/db/queries.py) + [stemming.py](src/anki_builder/stemming.py)). Tier 1 (`fts_exact`) and tier 3 (`stem`) query `sentences_fts`, which keeps accents (`remove_diacritics 0`), so `como` ≠ `cómodo`. Tier 2 (`like_substring`) queries the separate `text_fold` column — lowercased, vowel-accents stripped, **but ñ deliberately kept** (`año` ≠ `ano`) — so `come` matches inside the enclitic `cómelo`. The cascade returns the *first* non-empty tier.

- **`stemming.py` is shared between ingest and query on purpose.** `build.py` writes `stems_blob(text)` into the FTS `stems` column; `queries.py` stems the query word with the same functions. If you change tokenisation/stemming/folding, indexed stems and query stems must stay identical or the stem tier silently stops matching — and you must rebuild the DB.

- **The FreeDict glossary + inflection-proof `gloss_for` reuse the same stemmer** ([dictionary/freedict.py](src/anki_builder/dictionary/freedict.py), [db/build.py](src/anki_builder/db/build.py) `_load_glossary`, [db/queries.py](src/anki_builder/db/queries.py) `gloss_for`). `fetch-dumps` pulls the FreeDict `spa-eng` `.tar.xz` and extracts the `.tei`; `build-db` ingests it into the `glossary` table **only if the `.tei` is present** (optional, like `user_languages`), storing both `headword_fold` and `headword_stem` via the same `fold_accents`/`stem_word`. `gloss_for` is a two-tier cascade mirroring search: (1) exact-fold (nails lemmas: `comer`→*to eat*, `comida`→*food*), then (2) **stem-fallback** for inflected inputs absent as headwords (`comía`→stem `com`→*to eat*) — disambiguating stem collisions in Python by preferring a verb-POS headword (or `-ar/-er/-ir` ending), then the shortest. A miss returns `""` — and at **mining time** [pipeline/gloss.py](src/anki_builder/pipeline/gloss.py) `resolve_gloss` then asks the LLM for a short gloss (FreeDict is only ~4.5k headwords, so common words like `añadir` are absent), but **only** when a key is set and it's not an offline run (`--dry-run`/`--no-fallback`); a dict hit never calls the LLM, and a still-empty gloss is covered by the review gate's editable field. `cmd_run` prints `(LLM)` next to a model-supplied gloss.

- **`SentenceBlanked` mirrors the search tiers** ([cards.py](src/anki_builder/pipeline/cards.py) `_match_priority`): it blanks the target token using the same exact > accent-fold > folded-substring > stem priority, and blanks *all* tokens at the strongest matched level so a repeated word never leaves the answer visible.

- **Fan-out collapse (D7a):** one Spanish sentence with N English translations must yield exactly **one** candidate, not N. `filtered_candidates()` does this with a scalar subquery picking the shortest translation as the display value; alternates are kept in `candidates_json` for the future review swap UI. It also requires *both* audio AND a translation — that filter is why a word can hit a tier yet still route to `needs_fallback`.

- **Ingest is ordered to keep the DB small** ([db/build.py](src/anki_builder/db/build.py)): load target (`spa`) sentences → load the bilingual `spa-eng` links → load **only** the `eng` sentences those links reference. `build-db` rebuilds the corpus tables but **preserves `review_queue`**. `user_languages` is optional — absent it, the native-audio ranking boost simply disables (every candidate non-native).

- **Audio** ([tatoeba/audio.py](src/anki_builder/tatoeba/audio.py)): pure helpers build the CDN URL and the deterministic `tatoeba_spa_<id>.mp3` name (keyed by sentence id, always known); `download_audio` then caches the mp3 into `paths.media_cache` — CDN URL first, the `audio_id` `/audio/download/` endpoint on a 404. The mp3 is fetched lazily at review-playback and push time, never during `run`.

- **`anki/push.py` is the single push path, shared by the CLI and the web app.** `push_accepted(conn, cfg, …)` reads `accepted` rows, resolves+stores the mp3 (`storeMediaFile`, deterministic name = idempotent media), dedups on `Word` via `canAddNotes` (prints `skipped (already in deck)`; `--force`/`allowDuplicate` overrides, D12), then `addNote`. **`media_path_for_row(row, cfg)` is the shared media resolver** (also used by the review audio endpoint): a Tatoeba row (`chosen_sentence_id`) downloads+caches the native clip, while a fallback row (no sentence id, but an `audio_filename`) returns its already-cached TTS file — so both kinds push/serve audio uniformly. `anki/connect.py::AnkiClient.invoke` is the **only** method that touches the wire — tests monkeypatch it, so there is no live Anki in `pytest`. `ensure_model` only *creates* the [anki/model.py](src/anki_builder/anki/model.py) note type (D3); it does not update an existing model's templates.

- **The LLM fallback + vision share one OpenAI seam, mirroring `AnkiClient.invoke`** ([llm/client.py](src/anki_builder/llm/client.py), [llm/tts.py](src/anki_builder/llm/tts.py), [pipeline/fallback.py](src/anki_builder/pipeline/fallback.py)). `LLMClient._chat` and `TTSClient._speech` are the *only* wire methods (lazy `openai` import; the SDK's own `max_retries` covers 429/5xx) — tests monkeypatch exactly those, so `pytest` stays key/network-free. `cmd_run`'s `needs_fallback` branch calls `fallback.generate_fallback` **only** when `cfg.llm.fallback_enabled` AND `cfg.llm.api_key` are set and neither `--dry-run` nor `--no-fallback` is given; it generates a short sentence + gloss (prefers the LLM gloss, falls back to `gloss_for`) + TTS clip via `cards.build_fallback_fields` and enqueues `status=pending, flag=fallback` (still gated by D2 review). TTS failure → silent card, never aborts; LLM failure → falls through to the marked-only `needs_fallback`. `run --image` routes through `llm/vision.py::extract_words` (same `_chat` seam, JSON `{"words":[…]}`, capped at `cfg.llm.max_words`) into the unchanged per-word pipeline. JSON-schema misses raise `FallbackError`, not a crash.

- **The review app rebuilds card fields on swap/edit** ([review/server.py](src/anki_builder/review/server.py)): a *swap* reconstructs a `Candidate` from the stored `candidates_json` entry and re-runs `cards.build_card_fields` — preserving a human-edited `WordTranslation`, else repopulating it from `gloss_for`; an *edit* re-runs `blank_sentence` so the type-in `SentenceBlanked` stays consistent with an edited Word/Sentence (the `WordTranslation`/`Gloss` input flows through the generic field merge). Endpoints are sync and open their own SQLite connection per request (WAL is on). `create_app(cfg)` is a factory so tests drive it with a `TestClient` over a fixture DB.

## Config

`load_config` reads `./config.toml` if present (override with `--config` or `ANKI_BUILDER_CONFIG`), else falls back to dataclass defaults — so the tool and tests run with no config file. It also calls `load_dotenv()` (best-effort) so a gitignored `.env` supplies `OPENAI_API_KEY`. Copy `config.example.toml` → `config.toml` and `.env.example` → `.env` to override. **`config.toml`, `.env`, and `data/` are gitignored**; secrets (`OPENAI_API_KEY`) come only from the environment, never the TOML. **All sections are consumed now:** `[languages]`, `[paths]`, `[ranking]`, `[anki]`, and `[llm]`/`[tts]` (model, base_url, `fallback_enabled`, plus the cost caps `max_tokens`/`max_words`; TTS reuses the LLM key/base_url, D4).

## Conventions

- **Decision tags `D1`–`D13`** appear throughout docstrings; they trace back to [docs/specs/implementation_plan_v1.md](docs/specs/implementation_plan_v1.md). When changing pipeline behaviour, keep the tag/comment in sync.
- **The fixture corpus in [tests/conftest.py](tests/conftest.py) is the behavioural contract.** Each Spanish sentence is hand-picked to exercise one behaviour (accent distinction, enclitic, fan-out, audio/translation filter, ranking, native tie-break). If a behaviour change is intentional, update the fixture and the affected test in the same edit. Re-run `pytest` after touching the schema, `stemming.py`, search SQL, ranking weights, or blanking logic.
- Inter-stage data are plain dataclasses in [models.py](src/anki_builder/models.py) (`SearchResult`, `Candidate`, `CardFields`); `as_dict()` methods define the JSON shape stored in `review_queue`.
