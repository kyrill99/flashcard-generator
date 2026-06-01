# Personal Anki Card Builder — v1 spec (Spanish, corpus-driven)

**Goal:** feed in target Spanish words → get review-ready cards (word + real example sentence + native-speaker audio, in both recognition and cloze/type-in form) pushed into my Anki deck.

**Starting language:** Spanish (→ English translations have the best Tatoeba coverage; if my base language isn't English, coverage drops — keep the translation language a parameter).

## Core decision: sentences & audio come from Tatoeba, not the LLM
- **Why Tatoeba:** CC-BY, downloadable dumps, real native-speaker sentences with human translations, and — the deciding factor — **per-sentence native-speaker audio at a fixed URL**: `https://audio.tatoeba.org/sentences/spa/<sentence_id>.mp3`. No TTS, no wrong-pronunciation risk. Spanish is among the best-covered languages.
- **Personal use = ignore attribution.** CC-BY attribution only matters if I redistribute. For my own deck, skip it.
- **LLM's role shrinks to two jobs:** (1) image/text extraction in v1.1, (2) *fallback only* — generate a sentence + TTS when Tatoeba has no good match.

## How the pipeline changes (sentence-indexed, not word-indexed)
Tatoeba is indexed by sentence, my input is a word. So per word:
1. **Search** Spanish sentences containing the word.
2. **Filter** to sentences that have BOTH audio AND a translation in my base language.
3. **Rank** — prefer short/simple, native-speaker-owned sentences.
4. **Pick** the best; pull: Spanish sentence text, translation, audio mp3 URL.
5. **Fallback** if nothing suitable: LLM-generate a sentence + TTS, and **flag the card as fallback** in review.

## Card types (v1) — two cards from one Tatoeba record
1. **Recognition** — Front: target word. Back: translation + example sentence + audio.
2. **Cloze / type-in** — Front: the example sentence with the target word blanked (`Yo {{c1::como}} una manzana`), I type/recall it. Back: full sentence + translation + audio.

(This unifies the "type the answer" idea with cloze: blanking the known target word in the chosen sentence *is* the production card. Audio = full sentence on the back.)

## Data setup (do once)
- Download Tatoeba dumps for Spanish: **sentences** (spa), **links** (translation pairs), **sentences_with_audio** (which sentence IDs have audio + license + contributor — needed for the audio filter).
- Load into a local **SQLite** DB and query locally. Do NOT hammer the Tatoeba API per word — politeness + speed.
- Fetch each chosen sentence's audio on demand from the audio URL; copy the mp3 into Anki's `collection.media` folder at push time.

## The loop
input words → for each: Tatoeba search → filter (audio+translation) → rank → pick (or fallback) → build recognition + cloze cards → **review/edit/swap-sentence/delete** → push via AnkiConnect to chosen deck.

## Setup checklist
- Anki desktop + AnkiConnect add-on (code `2055492159`), Anki running when the script runs.
- Tatoeba dumps loaded into SQLite.
- An LLM API key (vision-capable, so the same key works for v1.1 image extraction).
- A TTS voice — **only used for fallback cards.** Pick a correct Spanish voice for those.

## Known limitations to design around (not bugs — accept or mitigate)
- **Coverage gaps:** rare/technical/slang words may have no audio+translation sentence. Mitigated by the LLM+TTS fallback, flagged in review.
- **Inflection matching:** "comer" appears as como/comes/comió; naive substring search misses conjugations and false-matches ("como" = "I eat" vs "like/as"). v1: search surface form, let review catch noise. Later: add spaCy Spanish lemmatizer.
- **Corpus errors / awkward sentences:** Tatoeba has known errors; "real" ≠ "good." **Review stays mandatory** — eyeball and swap the sentence if it's bad.

## NOT in v1
Image OCR (v1.1 — a vision call does extraction) · Telegram bot · mobile app · custom card-field builder · cost optimization · on-device model.

## The one rule (unchanged)
**Never let a card into the deck unreviewed.** SRS makes a wrong card permanent; unlearning costs more than the tool saves. The review step also lets me reject a bad Tatoeba sentence and pick another.

## v1.1 — image input
Add a vision-LLM step that extracts words (and detects marked/underlined words) from a photo, then feeds those words into the exact pipeline above. Nothing downstream changes.
