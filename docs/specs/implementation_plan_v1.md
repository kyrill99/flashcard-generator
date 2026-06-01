# Personal Anki Card Builder (Spanish, corpus-driven) — Implementation Plan

## Context

This is a greenfield project. The repo currently holds only two spec docs (`PRD-v1.md`, `personal_anki_builder_spec_v2.md`) and an empty git repo. The goal is a **personal** tool: feed in target Spanish words → get review-ready Anki cards (word + a _real native-speaker_ example sentence + audio, in both recognition and type-in/cloze form) pushed into a chosen deck via AnkiConnect.

The defining decision in the spec is that **sentences and audio come from Tatoeba dumps, not an LLM** — because Tatoeba gives real human sentences with human translations _and_ per-sentence native-speaker audio at a fixed URL, eliminating TTS mispronunciation risk. The LLM shrinks to two jobs: a _fallback_ sentence generator when Tatoeba has no good match, and (v1.1) image→word extraction. A human review gate is **mandatory and non-negotiable** — "never let a card into the deck unreviewed," because SRS makes a bad card a permanent cost.

This plan covers **v1** (text/word input → cards) and leaves a clean seam for **v1.1** (image input), exactly as the spec scopes it.

---

## Decisions & rationale (the user asked to document every decision)

### Confirmed with the user

- **D1 — Language/runtime: Python 3.11+.** Stdlib `sqlite3`, mature HTTP, the provider-agnostic OpenAI SDK (D4), `snowballstemmer`/spaCy for Spanish stemming and the future lemmatizer (`es_core_news_sm`), trivial CSV streaming, cross-platform on Windows. Every downstream idea in the PRD (server, Telegram bot) is Python-friendly.
- **D2 — Review UI: minimal local web app** (chosen). Review is audio-centric — you must _hear_ the native clip and be able to _swap_ among ranked candidate sentences before accepting. A browser page does inline `<audio>` playback + click-to-swap far better than a terminal, and it seeds the future app/bot direction. Built thin (FastAPI + one static page).
- **D3 — Card model: one custom note type → two card templates** (chosen). Anki's native Cloze note type can _only_ emit cloze cards, so it cannot also produce the recognition card from the same note. A custom 2-template note type gives: recognition + true type-in production (`{{type:Word}}`), full styling, a visible fallback flag, and both cards tied to one record. We replicate "blanking" with a precomputed `SentenceBlanked` field rather than `{{c1::}}`; we don't need Anki's multi-cloze engine since there is exactly one known target word.
- **D4 — Providers: OpenAI SDK (configurable model) + OpenAI cloud TTS** (chosen). We use the `openai` Python SDK as the LLM client rather than a vendor-locked one, because it talks to any OpenAI-compatible endpoint — so the **model and `base_url` are config values** and you can point it at OpenAI models, OpenRouter, a local server, etc., without code changes. The configured model must be **vision-capable** so the same client serves both the fallback sentence generator (v1) and v1.1 image→word extraction. Fallback audio uses OpenAI's TTS endpoint (same SDK/key); cost stays negligible because the Tatoeba path is the default. Both LLM and TTS sit behind thin interfaces so either can be swapped.

### Decisions made from the spec + research (no user input needed)

- **D5 — SQLite, queried locally; never hammer the Tatoeba API per word.** Straight from the spec. One-time ingest of dumps; all per-word work is local SQL.
- **D6 — Base/translation language is a parameter, default `eng`.** Spec requirement. English has the best Tatoeba coverage; keep `base_lang` configurable.
- **D7 — We must load base-language sentence texts too, not just Spanish.** _(Correction to the spec's "download sentences (spa)".)_ `links` only gives ID pairs; to _show_ the English translation we need the English sentence text. So the one-time ingest pulls per-language `sentences` exports for **both** the target (`spa`) and each base language (`eng`), plus the global `links` and `sentences_with_audio`. Documented as a deliberate addition.
  - **D7a — Collapse the translation fan-out (`GROUP BY` spa id).** A Spanish sentence frequently has _several_ English translations, so a naïve `spa → links → eng` join returns one row per translation — surfacing the _same audio sentence_ as multiple "candidates" and cluttering `rank.py`, `candidates_json`, and the review UI with duplicates. The candidate query therefore `GROUP BY`s the Spanish sentence id and collapses the English side to one display value (shortest translation via `MIN(length(text))`), keeping the alternates in `candidates_json` so review can still see them. **One unique Spanish sentence = exactly one candidate.** _(Adopted from design-review feedback.)_
- **D8 — Audio URL: the spec's CDN form `https://audio.tatoeba.org/sentences/spa/<sentence_id>.mp3`** (keyed by sentence_id, which we always have) is primary; `https://tatoeba.org/audio/download/<audio_id>` is the fallback endpoint. We still load `sentences_with_audio` because it is the **filter** (which sentence IDs have audio) and carries license/contributor used in ranking.
- **D9 — Tiered local search, escalating to the LLM only as a last resort.** Per word we cascade through _cheap local_ tiers and escalate only when all return nothing:
  1. **FTS5 exact-token** (`unicode61`, accents preserved) — precise, fast, the primary path. Matches the _token_ `como`, not substrings inside `cómodo`/`comodidad`.
  2. **`LIKE '%word%'` substring** — catches enclitic/agglutinated forms where the input is _contained_ in a longer token (`come` inside `cómelo`/`comerlo`) that token-matching misses.
  3. **Spanish Snowball stem** (pure-Python `snowballstemmer`; a stemmed-token column built at ingest) — the tier that actually recovers verb inflection (`comer`/`como`/`comió` share a stem), trading some precision for recall (mandatory review absorbs the noise).

  Only after all three yield zero do we hit the LLM fallback (D4). **Correcting a tempting shortcut** _(from design-review feedback):_ a `LIKE` tier alone does **not** fix the `comer`→`como` case, because `como` is not a substring of `comer` — that inflection class needs the stem/lemma tier, not substring search. Also, common infinitives and frequent conjugations already get ample FTS hits (sentences are full of `comer`, `como`, …), so the fallback spike is real only for _rare_ forms; the cascade keeps those local and cheap, preserving the Tatoeba-first strategy. Higher-precision **spaCy lemmatization** (`es_core_news_sm`) remains the documented upgrade over Snowball stemming.

- **D10 — Push audio via AnkiConnect `storeMediaFile`, not by copying into `collection.media`.** Robust, no need to discover the profile's media path, works even if Anki runs elsewhere. Deterministic filename `tatoeba_spa_<id>.mp3` for idempotent dedupe.
- **D11 — Pipeline runs as resumable stages with a persisted `review_queue`** (a table in the same SQLite DB), so `run` → `review` → `push` can happen in separate invocations and survive restarts. Review is mandatory (spec's "one rule"), so the queue is the gate.
- **D12 — Dedupe with `canAddNotes` + `allowDuplicate:false`**, keyed on the `Word` first field, before `addNote`. Prevents re-adding words already mined. _Accepted v1 limitation_ (from design-review feedback): keying on `Word` also blocks homonyms/polysemes — mining `banco` (bank) silently blocks `banco` (bench) later. To stop that being a mystery, `cli.py` **prints an explicit `skipped (already in deck): <word>` line for every deduped word**, and a `--force` / `--allow-duplicate` flag overrides the check so a known homonym can still be added. Sense-aware dedup (key on `Word`+`Sentence`, or per-sense tags) is a later refinement.
- **D13 — Native-speaker ranking signal is optional/loadable.** True "native-speaker-owned" needs `user_languages` (Lang, level, Username) joined to the audio contributor. v1 loads it if present and boosts native-contributed audio; otherwise ranking falls back to length/simplicity heuristics. Keeps v1 working without it.

---

## Architecture

```
flashcard-generator/
  pyproject.toml              # deps + console-script entry points
  config.example.toml         # deck, target/base lang, Anki note-type name, paths, ranking weights,
                              #   llm_model + base_url, tts voice, fallback toggle
  .env.example                # OPENAI_API_KEY (used for both LLM + TTS)
  data/
    dumps/                    # downloaded .tsv (gitignored)
    tatoeba.db                # SQLite: corpus + review_queue (gitignored)
  src/anki_builder/
    config.py                 # load TOML + env
    db/
      schema.sql              # tables + FTS5 + review_queue
      build.py                # D5/D7 one-time dump ingest
      queries.py              # D9 tiered search (FTS→LIKE→stem), D7a fan-out GROUP BY, audio+translation filter
    pipeline/
      search.py               # word -> candidate spa sentences (tiered FTS→LIKE→stem, D9)
      rank.py                 # D13 length/simplicity/native scoring -> ordered candidates
      select.py               # pick best; decide fallback
      fallback.py             # D4 OpenAI-SDK sentence + OpenAI TTS, tagged 'fallback'
      cards.py                # D3 build Word/Sentence/SentenceBlanked/Translation/Audio/Source/Flag
    tatoeba/audio.py          # D8 build URL, download+cache mp3
    anki/
      connect.py              # D10/D12 invoke(v6, :8765), ensure deck/model, storeMediaFile, addNote
      model.py                # D3 custom note type: fields, 2 templates, css
    llm/
      client.py               # OpenAI SDK client: configurable model + base_url; fallback sentence
      tts.py                  # OpenAI TTS for fallback audio (swappable)
      vision.py               # v1.1 stub: extract_words(image)->list[str] (same client/model)
    review/
      server.py               # D2 FastAPI: serve queue, audio, swap/edit/accept/delete, push
      static/index.html       # single-page review UI
    cli.py                    # entry points (below)
  tests/                      # fixture SQLite + unit/integration
```

### Database (SQLite)

- `sentences(id PK, lang, text)` — both `spa` and `eng` rows (D7).
- `links(sentence_id, translation_id)` — filtered on load to rows touching a `spa` sentence (cuts size).
- `audio(sentence_id PK, audio_id, username, license, attribution_url)` — filtered to `spa` (D8).
- `user_languages(username, lang, level)` — optional (D13).
- `sentences_fts` — FTS5 over `spa` text **plus a Snowball-stemmed token column**, `unicode61`, accents preserved (D9 tiers 1 & 3).
- `review_queue(word, status, chosen_sentence_id, candidates_json, fields_json, audio_filename, flag)` — D11 staging/gate.

### Per-word pipeline (the loop)

`word → tiered local search (FTS→LIKE→stem, D9) → filter to sentences with BOTH audio AND a base-lang translation, collapsed to one row per spa sentence (D7a) → rank (D13: shorter/simpler, native audio boost) → keep top N candidates → pick #1 (or fallback D4 only if all tiers empty) → build recognition + production card fields (D3) → enqueue to review_queue → REVIEW (D2, mandatory) → push via AnkiConnect (D10/D12)`.

### Custom note type (D3)

- Fields: `Word, Sentence, SentenceBlanked, Translation, Audio, Source, Flag`.
- Card 1 **Recognition** — Front `{{Word}}`; Back `{{Translation}}` + `{{Sentence}}` + `{{Audio}}`.
- Card 2 **Production** — Front `{{SentenceBlanked}}` + `{{type:Word}}`; Back `{{Sentence}}` + `{{Translation}}` + `{{Audio}}`.
- `createModel` with `isCloze:false`; created once if `modelNames` lacks it. `Flag` renders a visible "fallback" badge.

### CLI entry points

- `anki-builder build-db --langs spa,eng --dumps ./data/dumps` — one-time ingest (D5/D7).
- `anki-builder run --words words.txt` (or `--word comer`) — search→filter→rank→pick/fallback→build→enqueue.
- `anki-builder review` — launch the local web review app (D2); accepts push from there.
- `anki-builder push` / `--dry-run` — non-interactive push of accepted items; dry-run prints payloads without touching Anki.

---

## v1.1 readiness (designed-in, not built)

`llm/vision.py::extract_words(image) -> list[str]` feeds the exact same pipeline as a text word list — "nothing downstream changes" (spec). v1 ships a stub + the interface; v1.1 wires the vision call through the same OpenAI-SDK client (reusing the configured vision-capable model).

---

## Known limitations carried as designed (not bugs)

- **Coverage gaps** → LLM+TTS fallback, flagged in review (D4) — reached only after all local search tiers (D9) are empty.
- **Inflection** → v1 cascades FTS→LIKE→Snowball stem (D9) to keep most forms on the Tatoeba path; spaCy lemma index is the higher-precision later upgrade.
- **Homonyms / polysemes** → `Word`-keyed dedup blocks a second sense; surfaced via an explicit skip warning + `--force` override (D12).
- **Corpus errors / awkward sentences** → mandatory review with swap-to-next-candidate (D2/D11) is the mitigation.

---

## Build order

1. `pyproject.toml`, config loader, `schema.sql`.
2. `db/build.py` ingest + FTS (exact + Snowball-stemmed columns); verify counts against a couple of words.
3. `pipeline/` tiered search (D9) → fan-out-collapsed filter (D7a) → rank → select; unit tests on fixture DB.
4. `anki/connect.py` + `anki/model.py`; `--dry-run` card payloads.
5. `tatoeba/audio.py` download/cache.
6. `review/` web app (the gate) wired to `review_queue`.
7. `llm/client.py` + `llm/tts.py` (OpenAI SDK, configurable model/base_url) + `fallback.py` + flag plumbing.
8. `cli.py` entry points; `llm/vision.py` stub for v1.1.

---

## Verification (end-to-end)

- **Unit (fixture SQLite, no network):** tiered search — FTS exact (`como` ≠ `cómodo`), LIKE catches enclitic `cómelo`, stem recovers `como`←`comer`; fan-out collapse (one candidate per spa sentence despite N English translations, D7a); `SentenceBlanked` generation across accents/punctuation/casing; audio+translation filter; ranking order; fallback triggered **only** when all local tiers are empty; dedup skip prints a warning and `--force` overrides it.
- **AnkiConnect:** with Anki + AnkiConnect running, `version`/`deckNames` smoke test; `createDeck`/`createModel` idempotency; `canAddNotes` dedupe; one `storeMediaFile` + `addNote` round-trip. Mockable `invoke` layer for CI; `--dry-run` for no-Anki runs.
- **Manual happy path:** `build-db` on real spa+eng dumps → `run --word comer` → `review` (hear audio, swap once, accept) → `push` → confirm in Anki: 2 cards exist, audio plays, type-in works on the production card, fallback badge absent.
- **Fallback path:** `run --word <rare/invented>` → confirm LLM sentence + TTS audio generated, card flagged, still gated by review.
