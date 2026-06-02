-- Corpus + review staging schema (D5/D7/D8/D11/D13).
-- FTS5 with `remove_diacritics 0` keeps accents so `como` != `cómodo`/`cómo`.

PRAGMA journal_mode = WAL;

-- Both `spa` and base-language (`eng`) sentence rows live here (D7): `links`
-- only gives ID pairs, so we need the base text to *show* the translation.
CREATE TABLE IF NOT EXISTS sentences (
    id        INTEGER PRIMARY KEY,
    lang      TEXT NOT NULL,
    text      TEXT NOT NULL,
    -- Accent-folded, lowercased copy of `text`, populated only for the target
    -- language. Backs the LIKE substring tier (D9 tier 2); NULL for base-lang
    -- rows. A leading-wildcard LIKE can't use an index anyway, so none here.
    text_fold TEXT
);
CREATE INDEX IF NOT EXISTS idx_sentences_lang ON sentences (lang);

-- Translation pairs, filtered at load time to rows touching a `spa` sentence
-- to cut size (D7). Directionality in Tatoeba is symmetric per-row.
CREATE TABLE IF NOT EXISTS links (
    sentence_id    INTEGER NOT NULL,
    translation_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_sentence ON links (sentence_id);

-- Which `spa` sentence IDs have native-speaker audio + contributor/license,
-- used as the audio *filter* and in ranking (D8/D13). Keyed by sentence_id.
CREATE TABLE IF NOT EXISTS audio (
    sentence_id     INTEGER PRIMARY KEY,
    audio_id        INTEGER,
    username        TEXT,
    license         TEXT,
    attribution_url TEXT
);

-- Optional native-speaker signal (D13). Loaded if the dump is present; ranking
-- degrades gracefully to length/simplicity heuristics when empty.
CREATE TABLE IF NOT EXISTS user_languages (
    username TEXT NOT NULL,
    lang     TEXT NOT NULL,
    level    TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_languages ON user_languages (username, lang);

-- FTS5 over `spa` text (tier 1 exact-token) plus a Snowball-stemmed column
-- (tier 3). rowid == sentences.id. Accents preserved (D9).
CREATE VIRTUAL TABLE IF NOT EXISTS sentences_fts USING fts5 (
    text,
    stems,
    tokenize = "unicode61 remove_diacritics 0"
);

-- Offline FreeDict spa-eng word glosses (the L1 WordTranslation field). Stores
-- BOTH a folded key (exact-fold lookup) and a Snowball-stemmed key — the stemmed
-- key lets `gloss_for` recover inflected inputs (comía -> comer -> "to eat").
-- Loaded only if the `.tei` dump is present; lookup degrades to "" when absent.
CREATE TABLE IF NOT EXISTS glossary (
    headword      TEXT NOT NULL,
    headword_fold TEXT NOT NULL,
    headword_stem TEXT NOT NULL,
    gloss         TEXT NOT NULL,
    pos           TEXT
);
CREATE INDEX IF NOT EXISTS idx_glossary_fold ON glossary (headword_fold);
CREATE INDEX IF NOT EXISTS idx_glossary_stem ON glossary (headword_stem);

-- Resumable review gate (D11). One row per mined word awaiting human review.
CREATE TABLE IF NOT EXISTS review_queue (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    word               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    chosen_sentence_id INTEGER,
    candidates_json    TEXT,
    fields_json        TEXT,
    audio_filename     TEXT,
    flag               TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue (status);

-- Bookkeeping for the one-time ingest (row counts, dump mtimes).
CREATE TABLE IF NOT EXISTS ingest_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
