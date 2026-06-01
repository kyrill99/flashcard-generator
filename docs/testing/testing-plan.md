# Testing Plan — current state

Covers testing up to the **foundation pass**. The suite runs with **no network,
no Anki, and no API key** — a fixture SQLite corpus stands in for the real
dumps. This is the primary gate before wiring up external services.

```bash
uv run pytest            # 23 tests, ~0.5s
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
| `SentenceBlanked` (D3) | `test_cards.py` (6 cases) | exact, accents+neighbours, casing, enclitic, stem, all-occurrences, no-match |
| Card field assembly (D3) | `test_cards.py::test_build_card_fields` | all 7 fields incl. sound tag & source |
| Ingest counts + base filtering | `test_build.py::test_build_db_counts_and_filtering` | spa 2, eng 3 (999 dropped), links 3, audio 2, users 1 |
| Ingest → search → filter | `test_build.py::test_build_db_then_search_and_filter` | full path on a real ingest |
| Ingest idempotency | `test_build.py::test_build_db_is_idempotent` | rerun → no doubled rows |
| Review queue + dedup (D11/D12) | `test_queue.py` (2 cases) | enqueue, `word_in_queue`, deleted ≠ blocked |

**23 tests, all passing.** Every "Unit (fixture SQLite, no network)" item from
the plan is covered.

---

## 3. Manual / live verification (current state) — ✅ VERIFIED 2026-06-01

These need network and the real dumps (the user's manual step). They are **not**
part of `pytest`.

1. **Download + ingest**
   ```bash
   uv run anki-builder fetch-dumps
   uv run anki-builder build-db
   ```
   Expect non-zero counts for `target_sentences`, `base_sentences`, `links`,
   `audio` (a `user_languages` count of 0 just disables the native boost).

2. **Pipeline smoke**
   ```bash
   uv run anki-builder run --word comer --dry-run
   ```
   Expect a real Spanish sentence with an audio filename, the shortest English
   translation, and a sensible `blank:` line. Then:
   ```bash
   uv run anki-builder run --word zzqwx --dry-run   # → needs_fallback (deferred)
   uv run anki-builder run --word comer             # enqueues; rerun → "skipped (already in queue)"
   ```

3. **Spot-check the inflection cascade** on real data: pick a verb whose
   infinitive is rare in the corpus but whose conjugations are common, and
   confirm it still returns sentences (stem tier) rather than `needs_fallback`.

---

## 4. Not yet covered (out of scope for this pass)

These belong to later passes and have **no** tests yet:

- `fetch-dumps` network/decompression path (downloader is exercised only by a
  manual run; consider a mocked-`httpx` test next pass).
- mp3 download/cache (`tatoeba/audio.py` only has pure URL/filename helpers,
  which are covered indirectly via `test_build_card_fields`).
- AnkiConnect (`createDeck`/`createModel`/`canAddNotes`/`storeMediaFile`/
  `addNote`) — needs a mockable `invoke` layer + `--dry-run` payload tests.
- LLM fallback sentence + TTS generation, and the `fallback` flag end-to-end.
- The review web app.

---

## 5. Regression checklist

Re-run `uv run pytest` after any change to: the schema, `stemming.py`
(tokeniser/stemmer/fold), the search SQL, the ranking weights' meaning, or the
blanking logic. The fixture corpus is the contract — if a behaviour change is
intentional, update the fixture and the affected case together in the same edit.
