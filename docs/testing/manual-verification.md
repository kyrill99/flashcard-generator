# Manual Verification — corpus→Anki + two-card gloss

A hands-on checklist to confirm the features work end-to-end: **audio
download/cache (step 5)**, the **AnkiConnect push (step 4)**, the **review web
app (step 6)**, and the **two-card design with the FreeDict word gloss** (the
`WordTranslation` field, fetched by `fetch-dumps` and looked up by `gloss_for`).
The automated suite ([testing-plan.md](testing-plan.md)) already covers the
logic with everything mocked; this guide exercises the parts that need a real
Anki, a browser, and the network.

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

---

## Phase 0 — Automated gate (30 seconds)

- [ ] `uv run pytest` → **60 passed**. This validates all the logic (AnkiConnect
      client, the 8-field note type, push, audio, review API, and the FreeDict
      `gloss_for` lookup) with mocks before you touch live services.

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
- [ ] A word with no usable match (a `needs_fallback` row to see in review):
      ```bash
      uv run anki-builder run --word zzqwx        # → needs_fallback (deferred)
      ```
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
- [ ] **Fallback rows aren't pushable:** the `zzqwx` `needs_fallback` row has no
      fields — it is never pushed (the CLI only pushes `accepted`; "Push now" on
      it would report skipped). The live LLM/TTS fallback is a later pass.
- [ ] **Resumability (D11):** the three stages are independent invocations — you
      can `run` today, `review` later, `push` tomorrow; the queue persists in
      `data/tatoeba.db`.

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

---

## Sign-off

You've verified the corpus→Anki + gloss pass when: `pytest` is green (60),
`build-db` reports a non-zero `glossary` count, `run` prints a `gloss:` line
(and still resolves it for an inflected form via the stem-fallback), audio
caches to `data/media`, the review app lists/plays/swaps/edits the Gloss/accepts
/deletes (swap preserving an edited gloss), and an accepted card lands in Anki
as the 2 working cards described above (8 fields, gloss prompt, autoplay audio)
— pushed both from the CLI and the UI, with dedup and `--force` behaving as
described.
