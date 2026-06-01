# Documentation — implemented features

Reference for everything built in the foundation pass. For what is **not** yet
built, see [../status/implementation-status.md](../status/implementation-status.md).
Decision tags (D1–D13) refer to
[../specs/implementation_plan_v1.md](../specs/implementation_plan_v1.md).

---

## 1. Overview

The tool mines Anki cards from the **Tatoeba** corpus. Tatoeba is indexed by
sentence, but the input is a word, so per word the pipeline:

```
word
 └─ tiered local search (FTS exact → LIKE substring → Snowball stem)   [D9]
     └─ filter to sentences with BOTH audio AND a base-lang translation,
        collapsed to one candidate per Spanish sentence                [D7a]
         └─ rank (shorter/simpler first, native-audio boost)           [D13]
             └─ select #1 (or mark needs_fallback)                     [D4 seam]
                 └─ build card fields (incl. SentenceBlanked)          [D3]
                     └─ enqueue to review_queue                        [D11]
```

All per-word work is local SQL — the Tatoeba API is never touched at runtime
(D5). The corpus is built once from downloadable dumps.

---

## 2. Project layout

```
flashcard-generator/
├── pyproject.toml            # uv project + console script `anki-builder`
├── config.example.toml       # copy to config.toml to override defaults
├── .env.example              # OPENAI_API_KEY (used in a later pass)
├── README.md
├── docs/                     # this documentation set
├── data/                     # gitignored: dumps/, tatoeba.db, media/
├── src/anki_builder/
│   ├── config.py             # TOML + env → dataclasses
│   ├── stemming.py           # tokenize, Snowball stem, accent-fold
│   ├── models.py             # SearchResult, Candidate, CardFields
│   ├── db/
│   │   ├── __init__.py       # connect() + apply schema
│   │   ├── schema.sql        # tables + FTS5 + review_queue
│   │   ├── build.py          # dump ingest (build-db)
│   │   └── queries.py        # tiered search, D7a filter, queue helpers
│   ├── pipeline/
│   │   ├── search.py         # word → filtered candidates
│   │   ├── rank.py           # D13 scoring
│   │   ├── select.py         # pick best / mark fallback
│   │   └── cards.py          # D3 field construction + SentenceBlanked
│   ├── tatoeba/
│   │   ├── dumps.py          # fetch-dumps downloader
│   │   └── audio.py          # audio URL + media filename helpers
│   └── cli.py                # fetch-dumps, build-db, run, review/push (stub)
└── tests/                    # fixture-DB unit tests (no network)
```

---

## 3. CLI reference

All commands accept a global `--config PATH` (defaults to `./config.toml` if
present, else built-in defaults).

### `anki-builder fetch-dumps [--force]`
Downloads + decompresses the Tatoeba dumps into `paths.dumps_dir`. Skips files
that already exist unless `--force`. Uses the per-language exports, including the
**bilingual** `spa-eng_links` file. The optional `spa_user_languages` dump is
skipped on 404.

### `anki-builder build-db [--dumps DIR]`
One-time ingest of the dumps into `paths.db_path` (idempotent — rebuilds the
corpus tables, preserves `review_queue`). Prints row counts. Requires the four
core dumps; `user_languages` is optional.

### `anki-builder run (--word WORD | --words FILE) [--dry-run] [--force]`
Mines one or more words (a file is one word per line; `#` comments allowed).
Per word: search → filter → rank → select → build fields → enqueue. Prints a
summary line. `--dry-run` prints without writing; `--force` mines even if the
word is already queued (the dedup gate, D12). A word with no usable Tatoeba
candidate prints `needs_fallback (deferred)` and is enqueued with that status.

### `anki-builder review` / `anki-builder push`
Stubs — they print a notice. Implemented in the next pass.

---

## 4. Configuration

`config.example.toml` documents every key. Sections:

- **`[languages]`** — `target_lang` (default `spa`), `base_lang` (default `eng`,
  D6).
- **`[paths]`** — `db_path`, `dumps_dir`, `media_cache`. Relative paths resolve
  against the config file's directory.
- **`[ranking]`** (D13) — `length_weight`, `word_count_weight`,
  `native_audio_boost`, `ideal_min_words`, `ideal_max_words`, `candidates_kept`.
- **`[anki]`**, **`[llm]`**, **`[tts]`** — parsed now, consumed in later passes.

Secrets (`OPENAI_API_KEY`) come from the environment / `.env`, never the TOML.
Missing file or keys fall back to dataclass defaults, so the tool runs
out-of-the-box.

---

## 5. Database schema

Built by [db/schema.sql](../../src/anki_builder/db/schema.sql) and applied on
every `connect()`.

| Table | Purpose |
| --- | --- |
| `sentences(id, lang, text, text_fold)` | Both `spa` and `eng` rows (D7). `text_fold` is the accent-folded copy for the LIKE tier, populated only for the target language. |
| `links(sentence_id, translation_id)` | Translation pairs, canonicalised target→base at ingest (D7). |
| `audio(sentence_id, audio_id, username, license, attribution_url)` | The audio **filter** + ranking signal (D8). |
| `user_languages(username, lang, level)` | Optional native signal (D13). |
| `sentences_fts(text, stems)` | FTS5 (`unicode61 remove_diacritics 0`), `rowid = sentence id`. `text` = tier 1; `stems` = tier 3. |
| `review_queue(word, status, chosen_sentence_id, candidates_json, fields_json, audio_filename, flag, …)` | The mandatory review gate (D11). |
| `ingest_meta(key, value)` | Ingest bookkeeping (row counts). |

---

## 6. Search tiers (D9)

The cascade ([queries.py](../../src/anki_builder/db/queries.py) `search()`)
returns the **first** tier that yields hits:

1. **`fts_exact`** — `sentences_fts MATCH 'text:"word"'`. Exact token, accents
   preserved → `como` matches the token `como`, **not** `cómodo` or `cómo`.
2. **`like_substring`** — `text_fold LIKE '%fold(word)%'`. Accent-folded
   substring catches enclitics/agglutinated forms, e.g. `come` inside `cómelo`
   (folded `comelo`).
3. **`stem`** — `sentences_fts MATCH 'stems:"stem(word)"'`. Snowball stems
   recover inflection: `stem(comer) == stem(como) == 'com'`.

Only when all three are empty does a word route to the LLM fallback (a later
pass). Tokenisation, stemming, and folding all live in
[stemming.py](../../src/anki_builder/stemming.py) so ingest-time and query-time
processing are identical.

---

## 7. Filter + fan-out collapse (D7a)

`filtered_candidates()` keeps only sentences that have **both** audio (JOIN
`audio`) **and** at least one base-language translation (EXISTS on `links`). The
displayed translation is selected by a scalar subquery
(`ORDER BY length ASC LIMIT 1`), so a Spanish sentence with N English
translations yields **exactly one** `Candidate` — no duplicate "candidates" for
the same audio clip. The alternates are still available via
`translations_for()` and are stored in `candidates_json` for the review swap UI.

Candidate ids are staged in a temp table before the join, so the stem tier
returning thousands of ids never hits SQLite's bound-parameter limit.

---

## 8. Ranking (D13)

`rank.py` scores each candidate (higher = better):

```
score = native_boost(if native) − [ length_weight·chars
                                   + word_count_weight·words
                                   + out-of-band word penalty ]
```

Sorted best-first with deterministic tie-breaks (shorter text, then id). A
sentence is "native" when its audio contributor appears in `user_languages` at
the target language with level `5`. With no `user_languages` dump loaded, every
candidate is non-native and ranking degrades to pure length/simplicity — exactly
as D13 intends.

---

## 9. Card fields (D3)

`build_card_fields()` produces the 7 fields of the (future) custom note type:

| Field | Value |
| --- | --- |
| `Word` | the input word |
| `Sentence` | the chosen Spanish sentence |
| `SentenceBlanked` | the sentence with the target token(s) replaced by `____` |
| `Translation` | shortest base-language translation |
| `Audio` | `[sound:tatoeba_spa_<id>.mp3]` |
| `Source` | `Tatoeba #<id> · <contributor>` |
| `Flag` | `""` or `fallback` |

**`SentenceBlanked`** ([cards.py](../../src/anki_builder/pipeline/cards.py)
`blank_sentence()`) finds the target token by the same priority as the search
tiers — exact > accent-fold equal > folded-substring (enclitic) > same stem —
and blanks **all** tokens at the strongest matched level, so a word appearing
twice never leaves the answer visible. Punctuation, casing, and accents of the
surrounding text are preserved.

---

## 10. Audio (D8/D10)

[tatoeba/audio.py](../../src/anki_builder/tatoeba/audio.py) builds, from a
sentence id we always have:

- `audio_url(id)` → `https://audio.tatoeba.org/sentences/spa/<id>.mp3` (primary)
- `audio_fallback_url(audio_id)` → `https://tatoeba.org/audio/download/<audio_id>`
- `media_filename(id)` → `tatoeba_spa_<id>.mp3` (deterministic, for idempotent
  `storeMediaFile` dedupe, D10)
- `sound_tag(id)` → the `[sound:…]` field value

The actual mp3 download/cache and the Anki upload are deferred.

---

## 11. The review queue (D11)

`run` writes one `review_queue` row per word. `status` is `pending` (a usable
Tatoeba card) or `needs_fallback`. `candidates_json` holds the top-N ranked
candidates (each with its translation alternates and audio URL) for the future
review UI's swap-to-next-candidate action. Nothing is ever pushed to a deck
without passing this gate — "never let a card into the deck unreviewed."
