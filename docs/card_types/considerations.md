**Dictionary Misses on Inflected Verbs**

- **The Spec:** Card 2 relies on an exact L1 gloss (e.g., _to eat_ ) as the primary prompt for generation.
- **The Plan:** Utilizes an exact/accent-folded string match against the FreeDict offline dictionary (`WHERE headword_fold = ?`).
- **The Functional Impact:** Offline dictionaries typically index lemmas (root forms like _comer_ ). If your input word is a conjugated form found directly in the Tatoeba corpus (e.g., _comía_ or _comiendo_ ), the exact string match against the dictionary will likely fail, returning a blank gloss.
- **Recommendation:** The plan's "graceful degradation" (falling back to manual entry in the FastAPI review UI) is an excellent technical safety net. However, be prepared that for highly inflected languages like Spanish, you may be manually typing the gloss in the review gate more often than anticipated. If this friction becomes too high, you may eventually need to route the search word through a lemmatizer (like spaCy) before querying the FreeDict table.
