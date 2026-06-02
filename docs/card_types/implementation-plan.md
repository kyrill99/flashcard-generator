# Implementation plan — switch to the two-card design

Target design: the two cards in [card_types.md](card_types.md) — **Card 1: Contextual
Recognition** (L2→L1) and **Card 2: Productive Cloze with type-in** (L1→L2).

## Context

The note type today already emits **two cards** ("Recognition" + "Production") from one
record, so the bones match the spec. But the spec refines both cards and introduces a
piece of data the repo **does not have**: a short **L1 word gloss** (`comer` → `to eat`),
distinct from the sentence translation (`I like to eat apples.`). The spec uses that gloss
as the *answer hint* on Card 1's back and — more importantly — as the **L1→L2 prompt on
Card 2's front**. Without it, Card 2 is just a blank with no meaning cue.

Research confirmed the gloss **cannot be reliably derived from Tatoeba** (it is a parallel
*sentence* corpus — see `tatoeba/dumps.py` / `db/build.py`; no lexical layer). So we add a
small **offline bilingual dictionary** as a new corpus source.

### Decisions locked in
- **Card 1 front audio** = *reuse the full-sentence Tatoeba clip* (no isolated-word TTS —
  keeps the audio infrastructure unchanged).
- **Word gloss** = **offline dictionary**: **FreeDict `spa-eng` v0.3.1** — the TEI source
  (`freedict-spa-eng-0.3.1.src.tar.xz`, ~119 KB compressed). Tiny, clean, structured XML;
  fits the "build the corpus once, locally" model. The gloss field stays **editable in
  review**, so dictionary misses degrade gracefully (blank → you fill it), exactly like the
  optional `user_languages` native-speaker signal does today.

## Target fields & templates

Keep `Translation` meaning the **sentence** translation (minimizes churn); add one new field
`WordTranslation` for the gloss. New note-type `FIELDS` (8; `Word` stays first so AnkiConnect
dedupe is unchanged):

```
Word · WordTranslation · Sentence · SentenceBlanked · Translation · Audio · Source · Flag
```

**Card 1 — Contextual Recognition (L2→L1)**
- Front: `{{Word}}` + `{{Audio}}` (sentence clip; Anki autoplays audio on show)
- Back: `{{FrontSide}}` `<hr>` `{{WordTranslation}}` (= *to eat*) + `{{Sentence}}` +
  `{{Translation}}` (= *I like to eat apples.*) + flag badge

**Card 2 — Productive Cloze with type-in (L1→L2)**
- Front: `{{WordTranslation}}` (the prompt) + `{{SentenceBlanked}}` + `{{type:Word}}`
- Back: `{{FrontSide}}` (Anki renders the typed-vs-correct diff for `{{type:Word}}`) `<hr>`
  + `{{Sentence}}` + `{{Translation}}` + `{{Audio}}` (absent from this front, so it autoplays
  on reveal) + flag badge

## Implementation steps

### 1. Note type & templates — `anki/model.py`
- Add `"WordTranslation"` to `FIELDS` (second position).
- Rewrite `_RECOGNITION_FRONT/_BACK` and `_PRODUCTION_FRONT/_BACK` per the design above
  (move `{{Audio}}` onto the Recognition front; add `{{WordTranslation}}` to the Recognition
  back and the Production front).
- Add a `.gloss` CSS rule (bold L1 prompt). `note_payload` / `model_definition` need no logic
  change — both iterate `FIELDS`.

### 2. Card-fields data model — `models.py` + `pipeline/cards.py`
- `CardFields`: add `word_translation: str = ""` and `"WordTranslation": self.word_translation`
  in `as_dict()`.
- `build_card_fields(...)`: add keyword param `word_translation: str = ""` and set it on the
  returned `CardFields`. Blanking logic (`blank_sentence` / `_match_priority`) is untouched.

### 3. Offline dictionary — new source + table + lookup
- **New module `dictionary/freedict.py`** (mirrors `tatoeba/dumps.py`):
  - `fetch_dict(dumps_dir, force, log)` — download
    `https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz`,
    extract the `*.tei` member (stdlib `tarfile` + `lzma`) → `dumps_dir/spa-eng.tei`.
  - `parse_tei(path) -> Iterator[(headword, gloss, pos)]` — stdlib `xml.etree.ElementTree`,
    TEI namespace `http://www.tei-c.org/ns/1.0`: per `entry`, `orth` text = headword,
    `cit[@type='trans']/quote` texts = glosses (join the first sense's quotes with `, `).
- **Schema — `db/schema.sql`**: add
  ```sql
  CREATE TABLE IF NOT EXISTS glossary (
      headword TEXT NOT NULL, headword_fold TEXT NOT NULL, gloss TEXT NOT NULL, pos TEXT);
  CREATE INDEX IF NOT EXISTS idx_glossary_fold ON glossary (headword_fold);
  ```
- **Ingest — `db/build.py`**: add `glossary` to `_CORPUS_TABLES`, add `_load_glossary()`
  (uses `fold_accents` from `stemming.py` for `headword_fold`), call it in `build_db` **only
  if the `.tei` exists** (optional, like `user_languages`); add a `glossary` count to
  `IngestCounts`.
- **Query — `db/queries.py`**: add `gloss_for(conn, word) -> str` →
  `SELECT gloss ... WHERE headword_fold = ?` on `fold_accents(word.lower())`; return the first
  match or `""`.

### 4. Mining wiring — `cli.py`
- `cmd_fetch_dumps`: after the Tatoeba dumps, call `freedict.fetch_dict(...)` (tolerate
  failure — it's optional).
- `cmd_run`: `gloss = queries.gloss_for(conn, word)`; pass `word_translation=gloss` into
  `cards.build_card_fields(...)`; add the gloss to the printed summary line.

### 5. Review app — `review/server.py` + `review/static/index.html`
- **server `swap`**: rebuild fields while preserving any human-edited gloss —
  `word_translation = (row.get("fields") or {}).get("WordTranslation") or queries.gloss_for(c, row["word"])`.
- **server `edit`**: no special handling needed — `WordTranslation` flows through the existing
  field-merge; the `blank_sentence` re-run stays as-is.
- **index.html**: add an editable `WordTranslation` row (label "Gloss",
  `<input data-k="WordTranslation">`). `collectFields()` already harvests every
  `input[data-k]`, so edits are captured automatically. Optionally refresh it in the
  client-side `swap` handler.

### 6. Config & docs
- `config.py`: no new required key — the `.tei` lives under the existing `paths.dumps_dir`.
  (Optionally add a `dict_tei` path; not required.)
- Update `CLAUDE.md` (8 fields, the two-card design, the FreeDict source + `glossary` table +
  `gloss_for`), `docs/status/implementation-status.md`, and the `fetch-dumps` / `build-db`
  notes in `docs/README.md`. Add a one-line FreeDict attribution (CC-BY-SA / GPL).

### 7. Tests & fixtures
- `tests/conftest.py`: seed a few `glossary` rows (e.g. `comer`→`to eat`, `como`→`I eat`,
  `gato`→`cat`) so card/review tests can assert the gloss.
- `tests/test_cards.py`: assert `WordTranslation` in `as_dict()` and that
  `build_card_fields(..., word_translation=...)` populates it.
- `tests/test_anki.py`: update `inOrderFields` (8 fields); assert `{{WordTranslation}}` is in
  the Production **front** and the Recognition **back**, `{{Audio}}` is on the Recognition
  front, and `set(note["fields"]) == set(FIELDS)`.
- `tests/test_review.py`: assert queue rows expose `WordTranslation`; swap preserves/repopulates
  it; edit can set it.
- **New `tests/test_dictionary.py`**: `parse_tei` on a tiny inline TEI string; `gloss_for`
  (exact, accent-folded, miss→`""`). No network (fetch exercised with a monkeypatched client,
  like the audio tests).

## Verification
1. `uv run pytest` — full suite green (updated + new dictionary tests; still no
   network/Anki/API key needed).
2. Network path (manual, once): `uv run anki-builder fetch-dumps` pulls the FreeDict `.tei`;
   `uv run anki-builder build-db` reports a non-zero `glossary` count.
3. `uv run anki-builder run --word comer --dry-run` → summary shows the gloss (`to eat`) and
   the blanked sentence.
4. `uv run anki-builder review` → a card shows an editable **Gloss** field; edit + Accept
   persists it; **Swap** keeps the edited gloss.
5. With Anki + AnkiConnect running, `uv run anki-builder push` → the note type rebuilds with
   8 fields and both cards render: Card 1 (word + autoplay sentence audio → gloss + sentence +
   translation), Card 2 (gloss prompt + blank + type-in → diff + full sentence + translation +
   audio). Existing queued rows lacking `WordTranslation` push fine (missing field → `""`).

## Notes / out of scope
- No TTS and no LLM are built here (both remain the deferred step-7 fallback).
- `ensure_model` only **creates** a missing note type; it never updates an existing model's
  templates/fields. To see the new templates in an Anki profile that already has the old
  "AnkiBuilder Spanish" note type, either bump the name in `[anki].note_type` or update the
  model's fields/templates once inside Anki. (Worth a one-line callout in docs.)
- FreeDict `spa-eng` is a modest dictionary, so some words will miss — by design the gloss
  field is editable in the review gate to cover those.
