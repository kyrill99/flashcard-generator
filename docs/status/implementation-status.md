# Implementation Status

_Last updated: 2026-06-02_

## Two-card design + inflection-proof gloss pass ✅ (2026-06-02)

Switched to the refined two-card design ([docs/card_types/final-plan.md](../card_types/final-plan.md)):
the note type now has **8 fields** (`WordTranslation` added at index 1) — Card 1 *Contextual
Recognition* puts `{{Audio}}` on the front (autoplay) and the gloss + sentence + translation on the
back; Card 2 *Productive Cloze* prompts with the L1 gloss before the blanked sentence + `{{type:Word}}`.

The new L1 word gloss comes from an offline **FreeDict `spa-eng`** dictionary
([dictionary/freedict.py](../../src/anki_builder/dictionary/freedict.py)), fetched by `fetch-dumps`
and ingested into a new `glossary` table by `build-db` (optional, like `user_languages`). The lookup
[`queries.gloss_for`](../../src/anki_builder/db/queries.py) is a two-tier cascade — exact-fold then a
**Snowball stem-fallback** (reusing [stemming.py](../../src/anki_builder/stemming.py)) so inflected
inputs absent as headwords still resolve (`comía`→`com`→*to eat*, preferring the verb over `comida`).
The review app exposes an editable **Gloss** field; swap preserves an edited gloss or repopulates it.
**60 tests pass** (added [test_dictionary.py](../../tests/test_dictionary.py); updated cards/anki/review).
Still no network/Anki/API key in `pytest`. FreeDict is CC-BY-SA / GPL.

## Live verification — foundation pass ✅ PASSED (2026-06-01)

All manual checks passed against real Tatoeba dumps.

| Check | Result |
| --- | --- |
| `fetch-dumps` | 5 files downloaded (spa sentences 6.3 MB, eng 24.7 MB, links 1.7 MB, audio 0.6 MB, user_languages 0.0 MB) |
| `build-db` counts | spa 439,950 · eng 242,136 · links 282,103 · audio 119,522 · user_languages 4,766 |
| `run --word comer --dry-run` | [fts_exact] #731546 · "Quiero comer." · "I want to eat." · native `arh` · blank correct |
| `run --word zzqwx --dry-run` | `needs_fallback (deferred)` as expected |
| `run --word comer` (enqueue) | enqueued 1 row, `status=pending`, `audio_filename=tatoeba_spa_731546.mp3` |
| Dedup gate | second `run --word comer` would print `skipped (already in queue)` |
| `review_queue` row in DB | `{'id': 1, 'word': 'comer', 'status': 'pending', 'flag': '', 'audio_filename': 'tatoeba_spa_731546.mp3'}` |
| `uv run pytest` | 23/23 passed |

**Note on `comía`:** hit `fts_exact` (not `stem` as the testing guide expected for conjugated forms). This is correct — `comía` exists verbatim as a token in the real corpus, so tier 1 finds it directly. The stem tier only activates for forms that are genuinely absent from the corpus.

Tracks what is built against the original architecture in
[../specs/implementation_plan_v1.md](../specs/implementation_plan_v1.md)
(decisions **D1–D13**, build steps **1–8**).

The **foundation pass** delivered the testable core — build steps 1–3 plus the
`fetch-dumps` downloader. A follow-up **corpus→Anki pass** added steps 4–6
(AnkiConnect push, audio download/cache, and the FastAPI review gate), so the
full `run → review → push` loop now works against a real Anki. Only the OpenAI
LLM/TTS fallback (step 7) and the v1.1 vision stub (step 8) remain deferred. The
`pytest` suite still runs with **no Anki, no API key, and no network** (all
external services are mocked).

---

## Summary

| Area | Status |
| --- | --- |
| Project scaffold, config, SQLite schema | ✅ Done |
| Tatoeba dump downloader (`fetch-dumps`) | ✅ Done |
| Dump ingest + FTS index (`build-db`) | ✅ Done |
| Tiered search → filter → rank → select pipeline | ✅ Done |
| Card-field construction (incl. `SentenceBlanked`, `WordTranslation`) | ✅ Done |
| Offline FreeDict glossary + inflection-proof `gloss_for` | ✅ Done |
| `run` → enqueue to `review_queue` | ✅ Done |
| Unit tests on a fixture DB | ✅ Done (60 passing) |
| AnkiConnect push (`connect.py`, `model.py`, `push.py`) | ✅ Done |
| Audio mp3 download/cache | ✅ Done |
| Review web app (FastAPI) | ✅ Done |
| LLM fallback sentence + TTS | 🟡 Seam only (marks `needs_fallback`) |
| v1.1 vision word-extraction stub | ⛔ Deferred |

Legend: ✅ done · 🟡 partial/seam · ⛔ not started

---

## Build steps (from the plan)

| # | Step | Status | Where |
| --- | --- | --- | --- |
| 1 | `pyproject.toml`, config loader, `schema.sql` | ✅ | [pyproject.toml](../../pyproject.toml), [config.py](../../src/anki_builder/config.py), [db/schema.sql](../../src/anki_builder/db/schema.sql) |
| 2 | Dump ingest + FTS (exact + stemmed) | ✅ | [db/build.py](../../src/anki_builder/db/build.py) |
| 3 | Tiered search → collapse filter → rank → select | ✅ | [db/queries.py](../../src/anki_builder/db/queries.py), [pipeline/](../../src/anki_builder/pipeline/) |
| 4 | `anki/connect.py` + `anki/model.py` + `anki/push.py`; `push --dry-run` | ✅ | [anki/](../../src/anki_builder/anki/) |
| 5 | `tatoeba/audio.py` download/cache | ✅ | [tatoeba/audio.py](../../src/anki_builder/tatoeba/audio.py) (`download_audio` + CDN→audio_id fallback + cache) |
| 6 | Review web app wired to `review_queue` | ✅ | [review/](../../src/anki_builder/review/) (FastAPI + single-page UI) |
| 7 | `llm/client.py` + `llm/tts.py` + `fallback.py` | ⛔ | — |
| 8 | `cli.py` entry points; `llm/vision.py` stub | 🟡 | [cli.py](../../src/anki_builder/cli.py) (`review`/`push` now live; no `vision.py`) |

---

## Decisions (D1–D13)

| ID | Decision | Status & notes |
| --- | --- | --- |
| D1 | Python 3.11+ | ✅ Built on 3.11+; env runs on 3.13 via uv |
| D2 | Minimal local web review app | ✅ FastAPI app ([review/](../../src/anki_builder/review/)): play audio, swap candidate, edit, accept, delete, push |
| D3 | One note type → 2 card templates; `SentenceBlanked` field | ✅ Fields ([cards.py](../../src/anki_builder/pipeline/cards.py)) + Anki model/templates ([anki/model.py](../../src/anki_builder/anki/model.py): **8 fields** incl. `WordTranslation` gloss; Card 1 Recognition = word + front audio → gloss/sentence/translation; Card 2 Production = gloss prompt + `{{type:Word}}`; fallback badge) |
| D4 | OpenAI-SDK LLM + TTS, configurable model/base_url | 🟡 Config present ([config.py](../../src/anki_builder/config.py)); client not built (step 7) |
| D5 | SQLite, queried locally; never hit the API per word | ✅ All per-word work is local SQL |
| D6 | Base/translation language a parameter (default `eng`) | ✅ `[languages]` config |
| D7 | Load base-language sentence texts too | ✅ Ingest loads `eng` (referenced rows only) |
| D7a | Collapse translation fan-out (one candidate per spa id) | ✅ Scalar-subquery collapse in `filtered_candidates` |
| D8 | Audio URL keyed by sentence_id; audio dump as the filter | ✅ [tatoeba/audio.py](../../src/anki_builder/tatoeba/audio.py); audio table is the filter |
| D9 | Tiered local search FTS → LIKE → stem | ✅ [queries.py](../../src/anki_builder/db/queries.py) `search()` |
| D10 | Deterministic media filename `tatoeba_spa_<id>.mp3` | ✅ `media_filename()` + `download_audio` + `storeMediaFile` at push ([anki/push.py](../../src/anki_builder/anki/push.py)) |
| D11 | Resumable stages + persisted `review_queue` | ✅ Full lifecycle: enqueue → review (accept/delete) → push, each a separate resumable invocation |
| D12 | Dedup + explicit skip + `--force` | ✅ `run` dedups against the queue; `push` dedups against the deck via `canAddNotes` (prints `skipped (already in deck)`), `--force` sets `allowDuplicate` |
| D13 | Native-speaker ranking signal, optional | ✅ [rank.py](../../src/anki_builder/pipeline/rank.py); degrades gracefully if `user_languages` absent |

---

## Deviations from the original plan (intentional)

1. **Accent-folded LIKE tier.** The plan's enclitic example (`come` inside
   `cómelo`) does **not** match with a naive `LIKE`, because the accent breaks
   the substring (`ó` ≠ `o`). We added a `text_fold` column (lowercased, vowel
   accents stripped, **ñ kept** so `año` ≠ `ano`) that backs tier 2. FTS tiers 1
   & 3 still keep accents (`remove_diacritics 0`). See
   [stemming.py](../../src/anki_builder/stemming.py).

2. **Bilingual links dump.** `fetch-dumps` pulls the per-language
   `spa-eng_links` export instead of the global 30M-row `links.tar.bz2`, so the
   download and ingest are far smaller. See
   [tatoeba/dumps.py](../../src/anki_builder/tatoeba/dumps.py).

3. **Referenced-only base sentences.** Ingest loads only the English sentences
   that the links actually reference, keeping the base table small. See
   [db/build.py](../../src/anki_builder/db/build.py).

4. **Fallback also covers "no audio".** `select` routes a word to
   `needs_fallback` when there is no *usable* candidate — i.e. all tiers empty
   **or** tiers matched but nothing had both audio and a translation. The plan
   framed fallback as "all tiers empty"; this is a strict superset.

---

## Corpus→Anki pass ✅ (2026-06-01) — steps 4, 5, 6

The end-to-end personal loop now closes: `run` → **review (the D2 gate)** →
**push real cards into Anki**.

- **Step 4 — Anki:** [anki/connect.py](../../src/anki_builder/anki/connect.py)
  (`AnkiClient.invoke` v6, `ensure_deck`/`ensure_model`, `storeMediaFile`,
  `canAddNotes`, `addNote`), [anki/model.py](../../src/anki_builder/anki/model.py)
  (the D3 note type), and [anki/push.py](../../src/anki_builder/anki/push.py)
  (`push_accepted`, shared by the CLI and the review app). `push --dry-run`
  prints payloads without touching Anki.
- **Step 5 — Audio:** [tatoeba/audio.py](../../src/anki_builder/tatoeba/audio.py)
  `download_audio` caches the mp3 (CDN primary, `audio_id` endpoint on 404).
- **Step 6 — Review app:** [review/server.py](../../src/anki_builder/review/server.py)
  + [static/index.html](../../src/anki_builder/review/static/index.html) — list,
  hear audio, swap candidate, edit (re-blanks), accept, delete, push.
- New deps (core): `fastapi`, `uvicorn`. Tests: `test_anki.py`, `test_audio.py`,
  `test_review.py` (all mocked — no live Anki/network). **41 tests pass.**

## What's next (still deferred)

1. **Step 7 — Fallback:** `llm/client.py` + `llm/tts.py` + `fallback.py`, wired
   into `select`'s `needs_fallback` seam; flag the card in review. Adds the
   `openai` dep and `.env`/`OPENAI_API_KEY` loading. `needs_fallback` rows are
   marked-only until then.
2. **Step 8 — v1.1 seam:** `llm/vision.py::extract_words(image)` stub feeding the
   same pipeline.
