# Manual Verification — v1.1 (corpus→Anki, gloss, fallback, vision)

A hands-on checklist to confirm the features work end-to-end: **audio
download/cache (step 5)**, the **AnkiConnect push (step 4)**, the **review web
app (step 6)**, the **two-card design with the FreeDict word gloss** (the
`WordTranslation` field, fetched by `fetch-dumps` and looked up by `gloss_for`),
the **live LLM/TTS fallback (step 7)**, and **image word-extraction (step 8)**.
The automated suite ([testing-plan.md](testing-plan.md)) already covers the
logic with everything mocked; this guide exercises the parts that need a real
Anki, a browser, an OpenAI key, and the network.

Work top to bottom — each phase sets up the next. Tick the boxes as you go.

---

## Prerequisites

- [ ] `uv sync` has been run (deps incl. `fastapi`/`uvicorn` installed).
- [ ] The corpus DB exists at `data/tatoeba.db`. If not:
      ```bash
      uv run anki-builder fetch-dumps     # Tatoeba dumps + the FreeDict spa-eng .tei
      uv run anki-builder build-db        # expect non-zero spa/eng/links/audio AND glossary counts
      ```
      A zero `glossary` count means the FreeDict `.tei` didn't download — glosses
      will be blank (editable in review), but everything else still works.
- [ ] **For push only:** Anki desktop is **running** with the **AnkiConnect**
      add-on (code `2055492159`) installed. Test it once in a browser /
      PowerShell:
      ```powershell
      Invoke-RestMethod -Uri http://127.0.0.1:8765 -Method Post `
        -Body '{"action":"version","version":6}' -ContentType 'application/json'
      ```
      Expect `result = 6`. If this fails, push will fail too — fix it first.
- [ ] **For the fallback + vision phases (8) only:** an `OPENAI_API_KEY` in `.env`
      (copy `.env.example` → `.env`). `load_config` loads it automatically. Without
      it, `run` just marks `needs_fallback` and `run --image` errors — the rest of
      the guide needs no key.

---

## Phase 0 — Automated gate (30 seconds)

- [ ] `uv run pytest` → **85 passed**. This validates all the logic (AnkiConnect
      client, the 8-field note type, push, audio, review API, the FreeDict
      `gloss_for` lookup + LLM gloss fallback, and the LLM/TTS fallback + vision
      seams) with mocks before you touch live services.

---

## Phase 1 — Mine a few words (fills the review queue)

The review/push stages operate on `review_queue` rows, so create some:

- [ ] A word with audio + several candidates (good for the swap test):
      ```bash
      uv run anki-builder run --word comer
      uv run anki-builder run --word casa
      uv run anki-builder run --word agua
      ```
      Each prints `[tier] #<id> (<N> candidates)`, a `gloss:` line (e.g.
      *to eat* / *house* / *water*), and an `audio:` filename. **Note one
      `#<id>`** from the output — you'll reuse it in Phase 2.
- [ ] **Gloss stem-fallback** (the inflection fix): mine an *inflected* form whose
      lemma isn't a FreeDict headword and confirm the gloss still resolves:
      ```bash
      uv run anki-builder run --word comía --dry-run     # gloss: to eat (via comer)
      ```
      If `gloss:` shows `(none — edit in review)` for common words, the FreeDict
      `.tei` likely didn't ingest (re-check the `glossary` count from build-db).
- [ ] **LLM gloss fallback** (needs a key, see Phase 7 prereq): FreeDict is small
      (~4.5k headwords), so common words like `añadir` aren't in it. With a key set,
      a dict miss is filled by the LLM at mining time and printed with a `(LLM)` tag:
      ```bash
      uv run anki-builder run --word añadir        # gloss: to add (LLM)
      ```
      Offline (`--no-fallback`/`--dry-run`/no key) it stays `(none — edit in review)`.
- [ ] A word with no usable match (a marked-only `needs_fallback` row to see in
      review). `--no-fallback` forces the marker even if you have a key:
      ```bash
      uv run anki-builder run --word zzqwx --no-fallback   # → needs_fallback (marker only)
      ```
      (The live LLM fallback is exercised separately in Phase 7.)
- [ ] Re-running a queued word is skipped:
      ```bash
      uv run anki-builder run --word comer        # → skipped (already in queue)
      ```

---

## Phase 2 — Audio download + cache (step 5)

Download is lazy (it happens at review playback / push), but you can verify it
directly. Use the `#<id>` you noted above:

- [ ] Download a clip into the cache:
      ```bash
      uv run python -c "from anki_builder.tatoeba.audio import download_audio; print(download_audio(731546, lang='spa', cache_dir='data/media'))"
      ```
      (replace `731546` with your `#<id>`). Expect it to print a path under
      `data/media/`.
- [ ] The file exists and is a real mp3: check `data/media/tatoeba_spa_<id>.mp3`
      is present and non-empty (a few KB), and plays in any media player.
- [ ] **Cache hit:** run the same command again — it returns instantly (no
      re-download) and the same path.
- [ ] **Graceful miss:** a sentence id with no audio returns `None` rather than
      erroring (try a made-up id like `999999999`).

---

## Phase 3 — Review web app (step 6)

```bash
uv run anki-builder review        # → http://127.0.0.1:8000  (Ctrl+C to stop)
```

Open the page in a browser.

- [ ] **List:** the words you mined appear as cards, each showing Word, an audio
      player, editable Sentence/Translation/**Gloss**/Word, a "Blanked" preview,
      Source, and status.
- [ ] **Gloss field:** the **Gloss** input is pre-filled from FreeDict (e.g.
      `comer` → *to eat*) for words that hit the dictionary, and blank for misses.
- [ ] **Audio:** press play on a card — you hear the **native** clip. (This also
      proves step 5: a new `tatoeba_spa_<id>.mp3` lands in `data/media/`.)
- [ ] **Swap repopulates the gloss:** on a card with a **Swap** dropdown (only
      when it has >1 candidate) whose Gloss is **blank**, pick a different
      sentence — the Gloss fills in from the dictionary (same word → same gloss).
      Sentence, Translation, and the Blanked preview update, and the audio reloads.
- [ ] **Swap preserves an edited gloss:** type a custom Gloss, **Save edits**,
      then Swap again — your edited Gloss is **kept**, not overwritten.
- [ ] **Edit + re-blank:** change the Sentence (or Word) and click **Save edits**.
      The "Blanked" preview recomputes so the target word is blanked correctly in
      the edited sentence.
- [ ] **Filter:** switch the top dropdown to `needs_fallback` — the `zzqwx` row
      shows with a fallback badge and no audio. Switch to `all` to see everything.
- [ ] **Accept:** click **Accept** on one good card → toast, it leaves `pending`.
      Switch the filter to `accepted` and confirm it's there.
- [ ] **Delete:** click **Delete** on a card you don't want → it leaves the list
      (soft-deleted; the word can be re-mined later).

Leave at least one card **accepted** for Phase 4. Keep the server running (or
restart it later — the queue is persisted).

---

## Phase 4 — Push to Anki via the CLI (step 4)

Anki must be running (see Prerequisites).

- [ ] **Dry run first** (prints payloads, touches nothing):
      ```bash
      uv run anki-builder push --dry-run
      ```
      Expect a `[dry-run] would push <word>: {...}` line per accepted row and
      `pushed: N` in the summary, with **no** change in Anki.
- [ ] **Real push:**
      ```bash
      uv run anki-builder push          # → pushed: N, skipped: 0, errors: 0
      ```
- [ ] In Anki, confirm:
  - [ ] Note type **`AnkiBuilder Spanish`** exists with **8 fields** —
        `Word · WordTranslation · Sentence · SentenceBlanked · Translation ·
        Audio · Source · Flag` (Tools → Manage Note Types → Fields).
  - [ ] Deck **`Spanish::Mining`** exists and has the new note(s).
  - [ ] The note produces **2 cards**: **Recognition** and **Production**.
  - [ ] **Card 1 — Recognition (L2→L1):** front = the word **and the audio
        autoplays on show**; back adds the **gloss** (e.g. *to eat*) + the
        sentence + the sentence translation.
  - [ ] **Card 2 — Production (L1→L2):** front = the **gloss prompt** + the
        blanked sentence + a **type-in box**; typing the word and checking shows
        the typed-vs-correct diff; back shows the full sentence + translation +
        audio (**audio autoplays on reveal**, since it isn't on this front).
  - [ ] No fallback badge on these (they're real Tatoeba cards).
- [ ] **Pushed rows don't re-push:** run `uv run anki-builder push` again →
      `Nothing to push (no accepted rows).` (pushed rows are now `status=pushed`).

---

## Phase 5 — Push from the review UI

- [ ] In the review app, **Accept** another card, then click **Push now** on it →
      toast "Pushed to Anki"; it leaves the list. Confirm the card in Anki.
- [ ] With one or more accepted cards, click **Push all accepted** (top right) →
      toast reports `Pushed N, skipped M`. Confirm in Anki.

---

## Phase 6 — Dedup, force, resumability, fallback

- [ ] **Deck-level dedup (D12):** re-mine an already-pushed word into the queue,
      accept it, and push:
      ```bash
      uv run anki-builder run --word comer --force   # re-queue past the queue dedup
      ```
      Accept it in the UI, then `uv run anki-builder push` →
      `skipped (already in deck): comer` (because `canAddNotes` sees the Word).
- [ ] **Override with --force:** `uv run anki-builder push --force` → it adds the
      duplicate note (`allowDuplicate`). Confirm a second `comer` note in Anki.
- [ ] **Marker rows aren't pushable:** the `zzqwx --no-fallback` `needs_fallback`
      row has no fields — it is never pushed (the CLI only pushes `accepted`;
      "Push now" on it returns skipped).
- [ ] **Resumability (D11):** the three stages are independent invocations — you
      can `run` today, `review` later, `push` tomorrow; the queue persists in
      `data/tatoeba.db`.

---

## Phase 7 — Live LLM/TTS fallback (step 7, needs a key)

Requires `OPENAI_API_KEY` in `.env` and `[llm].fallback_enabled = true` (the
default). Pick a word with **no** Tatoeba match — an invented/very rare form.

- [ ] **Generate a fallback card:**
      ```bash
      uv run anki-builder run --word florgenbar
      ```
      Expect `… LLM fallback generated (flagged, pending review)` with an `ES:` /
      `EN:` line and an `audio:` filename (or `(TTS failed — silent)`). It enqueues
      a **`pending`** row (not `needs_fallback`) so it still hits review.
- [ ] **No-network guards still hold:** `run --word florgenbar2 --dry-run` prints
      `needs_fallback (deferred)` and `--no-fallback` writes the marker — neither
      calls the API.
- [ ] **Review the fallback card:** `uv run anki-builder review` → the row shows a
      **fallback** badge, the generated sentence/translation/gloss (all editable),
      and an **audio player that plays the TTS clip**. Edit if needed, **Accept**.
- [ ] **Push it:** `uv run anki-builder push` (or "Push now") → the card lands in
      Anki with the **fallback badge visible**, the TTS audio plays, and the
      type-in Production card works. `Source` reads `LLM fallback`.

## Phase 8 — Vision word-extraction (step 8, needs a key)

- [ ] **Mine words from an image:** take a photo/screenshot of some Spanish text
      (bonus: underline/highlight a few words) and run:
      ```bash
      uv run anki-builder run --image path/to/photo.jpg
      ```
      Expect the extracted words to be mined into the queue exactly like a word
      list — each prints its own `[tier] #id` / fallback line. If words are marked,
      only those are returned; otherwise all distinct words (capped at
      `[llm].max_words`).
- [ ] **Bad input is graceful:** `run --image does-not-exist.jpg` prints a clean
      `ERROR: could not read …` (exit 2), not a traceback.
- [ ] Open `review` and confirm the image-mined words appear as normal cards.

---

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `AnkiConnect request failed … Is Anki running?` | Anki not open, AnkiConnect not installed, or wrong `connect_url`. Verify with the `version` call in Prerequisites. |
| Audio won't play in review / `audio unavailable` | That sentence has no reachable mp3, or no network. Try Swap to another candidate; check `data/media/` is being populated. |
| `Address already in use` on `review` | Port 8000 is taken: `uv run anki-builder review --port 8123`. |
| `push` says "Nothing to push" | No rows are `status=accepted` — Accept some cards in the review app first. |
| `Corpus is empty` on `run` | Run `fetch-dumps` then `build-db`. |
| `gloss:` always blank / `glossary` count 0 | The FreeDict `.tei` didn't download or ingest. Re-run `fetch-dumps` (it logs the FreeDict URL), then `build-db`; a network/parse failure is logged but non-fatal. Glosses stay editable in review meanwhile. |
| `run` marks `needs_fallback` instead of generating a card | No `OPENAI_API_KEY` (check `.env`), `[llm].fallback_enabled = false`, or `--no-fallback` was passed. |
| `LLM fallback failed (…)` warning | The API call or its JSON was rejected (bad key, rate limit after retries, or off-schema response). The word degrades to a `needs_fallback` marker — fix the key/quota and re-run. |
| Fallback card pushes silent / `(TTS failed — silent)` | TTS errored; the card is still created and flagged. Re-run after fixing the key, or add audio manually in Anki. |
| `run --image` errors | Missing/unreadable file (clean `ERROR`), no key, or the model returned no `words`. Confirm the image path and key. |

---

## Sign-off

You've verified v1.1 when: `pytest` is green (85), `build-db` reports a non-zero
`glossary` count, `run` prints a `gloss:` line (and still resolves it for an
inflected form via the stem-fallback), audio caches to `data/media`, the review
app lists/plays/swaps/edits the Gloss/accepts/deletes (swap preserving an edited
gloss), and an accepted card lands in Anki as the 2 working cards described above
(8 fields, gloss prompt, autoplay audio) — pushed both from the CLI and the UI,
with dedup and `--force` behaving as described. **Plus (with a key):** a rare
word generates a flagged, review-gated LLM fallback card whose TTS audio plays
and pushes, and `run --image` mines an image's words into the queue.
