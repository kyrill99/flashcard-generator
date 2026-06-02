# Final plan — switch to the two-card design (with inflection-proof gloss lookup)

> Supersedes [implementation-plan.md](implementation-plan.md). Target design:
> [card_types.md](card_types.md). Fixes the concern in [considerations.md](considerations.md).

## Context

The note type today emits two cards ("Recognition" + "Production") from one record, but the
target design in [card_types.md](card_types.md) refines both and introduces data the repo
does **not** have: a short **L1 word gloss** (`comer` → *to eat*), distinct from the sentence
translation (*I like to eat apples.*). The gloss is the answer hint on Card 1's back and —
critically — the **L1→L2 prompt on Card 2's front**. Without it, Card 2 is a blank with no
meaning cue.

The gloss cannot be derived from Tatoeba (a parallel *sentence* corpus, no lexical layer), so
we add a small **offline FreeDict `spa-eng`** dictionary as a new corpus source, ingested once
by `build-db`, exactly like the existing dumps.

**The problem this plan fixes (from [considerations.md](considerations.md)):** FreeDict indexes
*lemmas* (`comer`), but mined words are often *inflected* (`comía`, `comiendo`, `comieron`). An
exact `headword_fold = ?` lookup would miss every conjugated form, forcing the human to
hand-type the gloss in review far too often.

**The fix: reuse the Snowball Spanish stemmer the repo already ships**
([stemming.py](../../src/anki_builder/stemming.py), used by search tier 3 and the blanking
logic). Verified empirically: `comer`, `comía`, `comiendo`, `comió`, `comido`, `comieron`,
`comería`, `coman`, `coma` **all stem to `com`**; `hablar/hablo/hablé/hablaba/hablando → habl`;
`vivir/vivo/vivía/viviendo → viv`; `gato/gatos → gat`. And `cómodo → comod` (distinct from
`com`, so no false collision with *comer*). So a **stem-fallback tier** on the gloss lookup
recovers inflected forms with **zero new dependencies** and no model downloads — keeping the
offline / no-network-tests philosophy intact (spaCy was considered and rejected for that reason).

### Locked decisions
- **Inflection** → Snowball stem-fallback tier in `gloss_for` (this plan's centrepiece).
- **Card 1 front audio** → reuse the full-sentence Tatoeba clip (no isolated-word TTS).
- **Model rollout** → clean slate; assume no notes/model exist yet. Just make the new 8-field,
  two-card definition the default. **No name-bump and no manual-migration caveat needed.**

## Target fields & templates

`Translation` keeps meaning the **sentence** translation (minimizes churn); add one new field
`WordTranslation` for the gloss. New `FIELDS` (8; `Word` stays first so AnkiConnect `Word`
dedupe is unchanged):

```
Word · WordTranslation · Sentence · SentenceBlanked · Translation · Audio · Source · Flag
```

**Card 1 — Contextual Recognition (L2→L1)**
- Front: `{{Word}}` + `{{Audio}}` (sentence clip; Anki autoplays on show)
- Back: `{{FrontSide}}` `<hr>` `{{WordTranslation}}` (= *to eat*) + `{{Sentence}}` +
  `{{Translation}}` (= *I like to eat apples.*) + flag badge

**Card 2 — Productive Cloze with type-in (L1→L2)**
- Front: `{{WordTranslation}}` (the prompt) + `{{SentenceBlanked}}` + `{{type:Word}}`
- Back: `{{FrontSide}}` (Anki renders the typed-vs-correct diff) `<hr>` + `{{Sentence}}` +
  `{{Translation}}` + `{{Audio}}` (absent from this front, so it autoplays on reveal) + flag badge

## Implementation steps

### 1. Note type & templates — [anki/model.py](../../src/anki_builder/anki/model.py)
- Insert `"WordTranslation"` into `FIELDS` at index 1 (after `Word`).
- Rewrite the four template constants per the design above: move `{{Audio}}` onto
  `_RECOGNITION_FRONT`; add `{{WordTranslation}}` to `_RECOGNITION_BACK` and prepend it to
  `_PRODUCTION_FRONT`. Add a `.gloss` CSS rule (bold L1 prompt).
- `model_definition` / `note_payload` need **no logic change** — both iterate `FIELDS`.

### 2. Card-fields data model — [models.py](../../src/anki_builder/models.py) + [pipeline/cards.py](../../src/anki_builder/pipeline/cards.py)
- `CardFields`: add `word_translation: str = ""` and `"WordTranslation": self.word_translation`
  in `as_dict()`.
- `build_card_fields(...)`: add keyword param `word_translation: str = ""`, set it on the
  returned `CardFields`. Blanking (`blank_sentence` / `_match_priority`) is untouched.

### 3. Offline dictionary source — new module `dictionary/freedict.py`
Mirror the shape of [tatoeba/dumps.py](../../src/anki_builder/tatoeba/dumps.py):
- `fetch_dict(dumps_dir, force, log)` — download
  `https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz`,
  extract the `*.tei` member (stdlib `tarfile` + `lzma`) → `dumps_dir/spa-eng.tei`.
- `parse_tei(path) -> Iterator[(headword, gloss, pos)]` — stdlib `xml.etree.ElementTree`,
  TEI ns `http://www.tei-c.org/ns/1.0`: per `entry`, `orth` text = headword;
  `cit[@type='trans']/quote` texts of the **first sense** joined with `, ` = gloss;
  `gramGrp/pos` (or `gramGrp/gram`) text = `pos` (may be empty).

### 4. Schema + ingest — [db/schema.sql](../../src/anki_builder/db/schema.sql) + [db/build.py](../../src/anki_builder/db/build.py)
- **Schema**: add a `glossary` table storing **both** a folded and a stemmed key (the stemmed
  key is what makes the inflection fix possible):
  ```sql
  CREATE TABLE IF NOT EXISTS glossary (
      headword      TEXT NOT NULL,
      headword_fold TEXT NOT NULL,
      headword_stem TEXT NOT NULL,
      gloss         TEXT NOT NULL,
      pos           TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_glossary_fold ON glossary (headword_fold);
  CREATE INDEX IF NOT EXISTS idx_glossary_stem ON glossary (headword_stem);
  ```
- **Ingest**: add `"glossary"` to `_CORPUS_TABLES` (so `build-db` resets it while still
  preserving `review_queue`); add `_load_glossary()` that, for each `parse_tei` row, writes
  `headword_fold = fold_accents(headword.lower())` and `headword_stem = stem_word(headword)`
  using the **same** `stemming.py` functions the search/blanking already use; call it from
  `build_db` **only if the `.tei` exists** (optional, exactly like `user_languages`); add a
  `glossary` count to `IngestCounts` + its `as_dict()`.

### 5. Inflection-proof gloss lookup — [db/queries.py](../../src/anki_builder/db/queries.py)  ← the fix
Add `gloss_for(conn, word) -> str`, a two-tier cascade mirroring the existing search cascade
and the `_match_priority` ordering in `cards.py`:

1. **Exact-fold tier** (precise) — `SELECT gloss FROM glossary WHERE headword_fold = ?
   ORDER BY rowid LIMIT 1` on `fold_accents(word.lower())`. Nails lemmas and uninflected inputs
   (`comer`→*to eat*, `gato`→*cat*, `comida`→*food*); `rowid` order = first/primary sense.
2. **Stem-fallback tier** (recovers inflection) — only if tier 1 is empty:
   `SELECT gloss, pos, headword FROM glossary WHERE headword_stem = ?` on `stem_word(word)`.
   Disambiguate stem collisions in Python (stem `com` matches both `comer` and `comida`): sort
   key `(0 if _is_verb else 1, len(headword))`, return the first gloss — i.e. **prefer a
   verb-POS headword, then the shortest** — because inflection misses are overwhelmingly verbs.
   `_is_verb` = `pos` names a verb **or** headword ends in `ar|er|ir`. So `comía` (stem `com`)
   → `comer` → *to eat*, not `comida`.
3. Miss → `""` (review gate's editable gloss covers the rest — graceful degradation).

Reuse `fold_accents` and `stem_word` from [stemming.py](../../src/anki_builder/stemming.py) —
do **not** re-implement, so indexed and queried keys stay identical (the same invariant the
search tiers depend on).

### 6. Mining wiring — [cli.py](../../src/anki_builder/cli.py)
- `cmd_fetch_dumps`: after the Tatoeba dumps, call `freedict.fetch_dict(...)` inside a
  try/except that logs and continues (it's optional).
- `cmd_run`: `gloss = queries.gloss_for(conn, word)`; pass `word_translation=gloss` into
  `cards.build_card_fields(...)`; add `gloss: <…>` to the printed summary line.

### 7. Review app — [review/server.py](../../src/anki_builder/review/server.py) + [review/static/index.html](../../src/anki_builder/review/static/index.html)
- **`swap` handler**: `build_card_fields` now needs the gloss. Preserve a human-edited gloss,
  else repopulate from the dictionary:
  `word_translation = (row.get("fields") or {}).get("WordTranslation") or queries.gloss_for(c, row["word"])`,
  passed as `word_translation=…` into `cards.build_card_fields(...)`.
- **`edit` handler**: no change — `WordTranslation` flows through the existing generic
  `{**old, **body.fields}` merge; the `blank_sentence` re-run stays as-is. (`update_row` /
  `_row_to_dict` already handle arbitrary `fields` keys via JSON — no `queries.py` change.)
- **index.html**: add one editable row
  `<div class="row"><label>Gloss</label><input data-k="WordTranslation" value="${esc(f.WordTranslation)}"></div>`.
  `collectFields()` auto-harvests every `input[data-k]`, so edits are captured. Optionally set
  the WordTranslation input from the returned row in the client-side `swap` handler (cosmetic).

### 8. Config & docs
- `config.py`: no new required key — the `.tei` lives under the existing `paths.dumps_dir`.
- Update [CLAUDE.md](../../CLAUDE.md) (8 fields, the two-card design, the FreeDict source +
  `glossary` table + the two-tier `gloss_for` and its stem-fallback),
  [docs/status/implementation-status.md](../status/implementation-status.md), and the
  `fetch-dumps` / `build-db` notes in [docs/README.md](../README.md). Add a one-line FreeDict
  attribution (CC-BY-SA / GPL).

### 9. Tests & fixtures
- `tests/conftest.py`: seed `glossary` rows incl. **a stem collision** so the verb-preference
  is exercised — e.g. `comer`→*to eat* (verb), `comida`→*food* (noun), `gato`→*cat*,
  `como`→*as, like* — writing `headword_fold`/`headword_stem` with the real
  `fold_accents`/`stem_word`.
- `tests/test_cards.py`: `WordTranslation` in `as_dict()`; `build_card_fields(..., word_translation=…)`
  populates it.
- `tests/test_anki.py`: `inOrderFields` is the 8 fields with `WordTranslation` second;
  `{{WordTranslation}}` appears in the **Production front** and the **Recognition back**;
  `{{Audio}}` is on the **Recognition front**; `set(note["fields"]) == set(FIELDS)`.
- `tests/test_review.py`: queue rows expose `WordTranslation`; swap preserves an edited gloss
  and repopulates from `gloss_for` when absent; edit can set it.
- **New `tests/test_dictionary.py`**: `parse_tei` on a tiny inline TEI string; and `gloss_for`
  covering **exact** (`comer`→*to eat*), **accent-fold**, **stem-fallback with verb-preference**
  (`comía`→*to eat*, beating `comida`), and **miss**→`""`. `fetch_dict` exercised with a
  monkeypatched downloader (no network), like the audio tests.

## Verification
1. `uv run pytest` — full suite green (updated + new `test_dictionary.py`; still no
   network/Anki/API key). Proves the stem-fallback disambiguation end-to-end on the fixture.
2. Network path (manual, once): `uv run anki-builder fetch-dumps` pulls the FreeDict `.tei`;
   `uv run anki-builder build-db` reports a non-zero `glossary` count.
3. `uv run anki-builder run --word comer --dry-run` → summary shows gloss *to eat* + blank.
   Then `uv run anki-builder run --word comía --dry-run` → summary **still** shows *to eat*
   (proves the stem-fallback fix; no manual entry needed for the inflected form).
4. `uv run anki-builder review` → a card shows an editable **Gloss** field; edit + Accept
   persists it; **Swap** keeps the edited gloss (or repopulates from the dictionary if blank).
5. With Anki + AnkiConnect running, `uv run anki-builder push` → a fresh note type is created
   with 8 fields; Card 1 (word + autoplay sentence audio → gloss + sentence + translation) and
   Card 2 (gloss prompt + blank + type-in → diff + full sentence + translation + audio) render.

## Notes / out of scope
- No TTS and no LLM are built here (both remain the deferred step-7 fallback).
- FreeDict `spa-eng` is modest, so some words still miss (and stem collisions for non-verbs can
  pick a sub-optimal sense) — by design the gloss field is editable in the review gate, so a
  wrong/blank gloss degrades gracefully to a one-field human edit.
- Per the locked decision, no note-type migration logic: we assume a clean Anki profile, so
  `ensure_model` simply creates the new model on first push.
