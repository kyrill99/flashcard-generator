# Testing Plan — current state

Covers testing through the **two-card + gloss pass** (corpus→Anki, build steps
1–6, plus the two-card note type and the FreeDict word-gloss lookup). The suite
runs with **no network, no Anki, and no API key** — a fixture SQLite corpus
stands in for the real dumps, and AnkiConnect / audio HTTP / the FreeDict
download are all mocked. This is the primary gate before any live run.

```bash
uv run pytest            # 60 tests, ~3s
uv run pytest -v         # per-test names
uv run pytest tests/test_search.py::test_like_catches_enclitic   # one case
```

---

## 1. Test fixtures

[tests/conftest.py](../../tests/conftest.py) builds a small in-memory corpus
(`corpus` fixture) hand-crafted so each sentence exercises a specific behaviour:

| Spa id | Sentence | Exercises |
| --- | --- | --- |
| 1 | `Yo como una manzana.` | token `como`; **two** English translations (collapse) |
| 2 | `El sofá es muy cómodo.` | `cómodo` must NOT match `como` |
| 3 | `¡Cómelo, por favor!` | enclitic `cómelo` (LIKE tier) |
| 4 | `Esto no tiene audio.` | translation but **no audio** (filtered out) |
| 5 | `Sin traducción aquí.` | audio but **no translation** (filtered out) |
| 6 / 7 | `El gato duerme.` / long version | short+native vs long+non-native (ranking) |
| 8 / 9 | `Tengo un perro.` (×2) | identical text, native vs non-native (boost tie-break) |

Contributor `nat` is a spa native (level 5); `foo` is not.

It also seeds a small **`glossary`** (FreeDict stand-in), written with the real
`fold_accents`/`stem_word` so the lookup keys match production:

| Headword | Gloss | pos | Exercises |
| --- | --- | --- | --- |
| `comer` | to eat | verb | lemma; **verb** in the `com` stem collision |
| `comida` | food | noun | exact-fold beats the stem tier; loses to the verb on a stem miss |
| `como` | as, like | conj | also stems to `com` (verb-preference must skip it) |
| `gato` | cat | noun | plain lemma |
| `rápido` | fast | adj | accented headword → accent-fold lookup (`rapido` → *fast*) |

---

## 2. Coverage matrix

Maps directly to the plan's **Verification → Unit** checklist
([../specs/implementation_plan_v1.md](../specs/implementation_plan_v1.md)).

| Area (decision) | Test file · case | Asserts |
| --- | --- | --- |
| FTS exact, accents (D9 t1) | `test_search.py::test_fts_exact_matches_token_not_substring` | `como` → {1}, not 2/3 |
| | `test_search.py::test_fts_exact_accent_distinct` | `cómodo` → {2} |
| LIKE enclitic (D9 t2) | `test_search.py::test_like_catches_enclitic` | `come` → {3} via fold |
| Stem inflection (D9 t3) | `test_search.py::test_stem_recovers_inflection` | `comer` → {1} |
| No match → fallback marker | `test_search.py::test_no_match_returns_no_tier` | tier `None`, ids `[]` |
| Fan-out collapse (D7a) | `test_filter_rank.py::test_fanout_collapse_one_candidate_per_sentence` | 1 candidate, shortest translation; alternates ordered |
| Audio+translation filter | `test_filter_rank.py::test_filter_requires_audio_and_translation` | only {1}; 4 & 5 excluded |
| Ranking order (D13) | `test_filter_rank.py::test_ranking_prefers_short_and_native` | [6, 7]; native flags |
| Native boost tie-break (D13) | `test_filter_rank.py::test_native_boost_breaks_a_tie` | 8 before 9 |
| Select ok / fallback (D4 seam) | `test_filter_rank.py::test_select_ok_and_fallback` | picks 1; invented → fallback |
| `SentenceBlanked` (D3) | `test_cards.py` (7 cases) | exact, accents+neighbours, casing, enclitic, stem, all-occurrences, no-match |
| Card field assembly (D3) | `test_cards.py::test_build_card_fields*` (2 cases) | all **8** fields incl. sound tag & source; `word_translation` populates `WordTranslation` distinct from `Translation` |
| Ingest counts + base filtering | `test_build.py::test_build_db_counts_and_filtering` | spa 2, eng 3 (999 dropped), links 3, audio 2, users 1 |
| Ingest → search → filter | `test_build.py::test_build_db_then_search_and_filter` | full path on a real ingest |
| Ingest idempotency | `test_build.py::test_build_db_is_idempotent` | rerun → no doubled rows |
| Review queue + dedup (D11/D12) | `test_queue.py` (2 cases) | enqueue, `word_in_queue`, deleted ≠ blocked |
| Audio download/cache (D8, step 5) | `test_audio.py` (5 cases) | CDN 200 caches; 404→`audio_id` fallback; 404+no id→None; cache hit = no request; `force` re-downloads |
| Two-card note type (D3, step 4) | `test_anki.py::test_model_definition_shape` · `test_note_payload_carries_all_fields_and_dedup_option` | **8** fields (`WordTranslation` 2nd), 2 templates, `isCloze=False`; `{{Audio}}` on Recognition **front** + gloss on its back; gloss prompt + `{{type:Word}}` on Production front; payload shape + `allowDuplicate` |
| AnkiConnect client (step 4) | `test_anki.py::test_ensure_model_is_idempotent` | `createModel` only when absent |
| Push + dedup (D10/D12, step 4) | `test_anki.py::test_push_*` (6 cases) | adds note + stores media + marks `pushed`; `canAddNotes` False skips; `--force` overrides; `--dry-run` writes nothing; explicit `row_ids` still honour the accept gate; empty `canAddNotes` skips (no IndexError) |
| Gloss lookup + dictionary | `test_dictionary.py` (7 cases) | `parse_tei` (first-sense quotes, skips no-trans); `gloss_for` exact / accent-fold / **stem-fallback prefers verb** (`comía`→*to eat*) / miss→`""`; `fetch_dict` extracts the `.tei` from a mocked `.tar.xz` + cache-skip |
| Review app (D2, step 6) | `test_review.py` (14 cases) | queue list exposes `WordTranslation`; swap rebuilds fields/audio and **preserves an edited gloss else repopulates from `gloss_for`**; edit re-blanks + sets gloss; accept/delete flip status; push via mocked invoke; index served; CSRF + DNS-rebinding guards (M1); terminal-status / pending-promote push rules (M2) |

**60 tests, all passing** (search 5 · filter_rank 5 · cards 9 · build 4 · queue
2 · audio 5 · anki 9 · review 14 · dictionary 7). Every "Unit (fixture SQLite,
no network)" item from the plan is covered, plus mocked coverage of
Anki/audio/review and the FreeDict gloss lookup.

---

## 3. Manual / live verification (current state) — ✅ VERIFIED 2026-06-01

These need network and the real dumps (the user's manual step). They are **not**
part of `pytest`. For a full step-by-step checklist of the corpus→Anki features
(audio, push, review app — steps 4–6), see
[manual-verification.md](manual-verification.md).

1. **Download + ingest**
   ```bash
   uv run anki-builder fetch-dumps    # Tatoeba dumps + the FreeDict spa-eng .tei
   uv run anki-builder build-db
   ```
   Expect non-zero counts for `target_sentences`, `base_sentences`, `links`,
   `audio`, and **`glossary`** (a `user_languages` count of 0 just disables the
   native boost; a `glossary` count of 0 just disables the gloss lookup).

2. **Pipeline smoke**
   ```bash
   uv run anki-builder run --word comer --dry-run
   ```
   Expect a real Spanish sentence with an audio filename, the shortest English
   translation, a `gloss:` line (e.g. *to eat*), and a sensible `blank:` line.
   Then:
   ```bash
   uv run anki-builder run --word comía --dry-run   # gloss STILL "to eat" (stem-fallback)
   uv run anki-builder run --word zzqwx --dry-run   # → needs_fallback (deferred)
   uv run anki-builder run --word comer             # enqueues; rerun → "skipped (already in queue)"
   ```

3. **Spot-check the inflection cascade** on real data: pick a verb whose
   infinitive is rare in the corpus but whose conjugations are common, and
   confirm it still returns sentences (stem tier) rather than `needs_fallback`.
   The same stem-fallback backs `gloss_for`, so an inflected form absent from
   FreeDict's headwords (e.g. `comía`) should still print the lemma's gloss.

4. **Review → push happy path** (needs Anki + the AnkiConnect add-on
   `2055492159` running):
   ```bash
   uv run anki-builder run --word comer
   uv run anki-builder review            # open http://127.0.0.1:8000
   #   → hear the native clip, swap once, edit the Gloss field, Accept
   uv run anki-builder push              # or the UI's Push button
   uv run anki-builder push --dry-run    # prints payloads, touches nothing
   ```
   Confirm in Anki: the `AnkiBuilder Spanish` note type (**8 fields**) +
   `Spanish::Mining` deck exist, **2 cards** — Card 1 *Recognition* (word +
   autoplaying sentence audio → gloss + sentence + translation), Card 2
   *Production* (gloss prompt + blanked sentence + type-in → diff + full
   sentence + translation + audio). No fallback badge. Re-push →
   `skipped (already in deck)`; `--force` overrides. Full checklist:
   [manual-verification.md](manual-verification.md).

---

## 4. Not yet covered (out of scope for this pass)

These belong to later passes and have **no** tests yet:

- The **Tatoeba** `fetch-dumps` network/decompression path (downloader is
  exercised only by a manual run). *(The FreeDict downloader **is** now covered:
  `test_dictionary.py::test_fetch_dict_*` drives `fetch_dict` over a mocked
  `httpx` transport, including the `.tar.xz` → `.tei` extraction.)*
- A **live** AnkiConnect round-trip (the unit tests mock `invoke`; the real
  `createModel`/`addNote` path is exercised only by the manual happy path below).
- LLM fallback sentence + TTS generation, and the `fallback` flag end-to-end
  (step 7, deferred).

---

## 5. Regression checklist

Re-run `uv run pytest` after any change to: the schema, `stemming.py`
(tokeniser/stemmer/fold — it backs both the search stem tier **and** the
`glossary` keys + `gloss_for`), the search SQL, the ranking weights' meaning,
the blanking logic, the note-type fields/templates, the `gloss_for` cascade, or
the FreeDict TEI parsing. The fixture corpus + glossary are the contract — if a
behaviour change is intentional, update the fixture and the affected case
together in the same edit. **Rebuild the DB** (`build-db`) after any change to
`stemming.py` or the schema, or the indexed keys go stale.
