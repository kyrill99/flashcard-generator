"""Command-line entry points (D11 resumable stages).

    anki-builder fetch-dumps         download Tatoeba dumps
    anki-builder build-db            one-time ingest into SQLite
    anki-builder run --word comer    search -> filter -> rank -> select -> enqueue
    anki-builder review / push       (stubbed; arrive in the next pass)

`run` is the search/build/enqueue stage. The mandatory review gate and the push
to Anki are deferred, so `run` stops at writing review_queue rows and printing a
per-word summary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, db
from .config import Config, load_config
from .db import build as db_build
from .db import queries
from .pipeline import cards, rank, search, select
from .tatoeba import dumps as tatoeba_dumps
from .tatoeba.audio import audio_url, media_filename


def _load_words(args) -> list[str]:
    if args.word:
        return [args.word]
    words: list[str] = []
    for line in Path(args.words).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def _corpus_size(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]


# --- commands --------------------------------------------------------------


def cmd_fetch_dumps(args, cfg: Config) -> int:
    tatoeba_dumps.fetch_dumps(
        cfg.paths.dumps_dir,
        target_lang=cfg.languages.target_lang,
        base_lang=cfg.languages.base_lang,
        force=args.force,
    )
    print(f"\nDumps ready in {cfg.paths.dumps_dir}")
    return 0


def cmd_build_db(args, cfg: Config) -> int:
    dumps_dir = Path(args.dumps) if args.dumps else cfg.paths.dumps_dir
    counts = db_build.build_db(
        cfg.paths.db_path,
        dumps_dir,
        target_lang=cfg.languages.target_lang,
        base_lang=cfg.languages.base_lang,
    )
    print(f"\nIngest complete -> {cfg.paths.db_path}")
    for key, value in counts.as_dict().items():
        print(f"  {key:18} {value:>10,}")
    if counts.audio == 0:
        print("\nWARNING: zero audio rows — check the audio dump.", file=sys.stderr)
    return 0


def cmd_run(args, cfg: Config) -> int:
    target = cfg.languages.target_lang
    base = cfg.languages.base_lang

    conn = db.connect(cfg.paths.db_path)
    if _corpus_size(conn) == 0:
        print(
            "Corpus is empty. Run `anki-builder fetch-dumps` then "
            "`anki-builder build-db` first.",
            file=sys.stderr,
        )
        conn.close()
        return 2

    words = _load_words(args)
    enqueued = fallbacks = skipped = 0

    for word in words:
        if not args.force and queries.word_in_queue(conn, word):
            print(f"skipped (already in queue): {word}")
            skipped += 1
            continue

        result, candidates = search.find_candidates(
            conn, word, target_lang=target, base_lang=base
        )
        ranked = rank.rank(candidates, cfg.ranking)
        selection = select.select(
            result, ranked, candidates_kept=cfg.ranking.candidates_kept
        )

        if selection.needs_fallback:
            print(f"{word}: no Tatoeba match — needs_fallback (deferred)")
            fallbacks += 1
            if not args.dry_run:
                queries.enqueue(
                    conn,
                    word=word,
                    status=select.STATUS_NEEDS_FALLBACK,
                    chosen_sentence_id=None,
                    candidates=None,
                    fields=None,
                    audio_filename=None,
                    flag="fallback",
                )
            continue

        chosen = selection.chosen
        fields = cards.build_card_fields(word, chosen, target_lang=target)
        audio_filename = media_filename(chosen.sentence_id, target)

        candidate_payload = []
        for c in selection.kept:
            entry = c.as_dict()
            entry["translations"] = queries.translations_for(conn, c.sentence_id, base)
            entry["audio_url"] = audio_url(c.sentence_id, target)
            candidate_payload.append(entry)

        print(
            f"{word}: [{selection.tier}] #{chosen.sentence_id} "
            f"({len(selection.kept)} candidates)\n"
            f"    ES: {chosen.spa_text}\n"
            f"    {base.upper()}: {chosen.translation}\n"
            f"    blank: {fields.sentence_blanked}\n"
            f"    audio: {audio_filename}"
            + (f"  · native: {chosen.username}" if chosen.is_native else "")
        )

        if not args.dry_run:
            queries.enqueue(
                conn,
                word=word,
                status="pending",
                chosen_sentence_id=chosen.sentence_id,
                candidates=candidate_payload,
                fields=fields.as_dict(),
                audio_filename=audio_filename,
                flag=fields.flag,
            )
            enqueued += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    mode = "(dry-run, nothing written) " if args.dry_run else ""
    print(
        f"\nDone {mode}— enqueued: {enqueued}, fallback: {fallbacks}, "
        f"skipped: {skipped}"
    )
    return 0


def cmd_stub(args, cfg: Config) -> int:
    print(
        f"`{args.command}` arrives in the next pass (AnkiConnect + review web "
        "app). This foundation pass stops at the review_queue.",
        file=sys.stderr,
    )
    return 0


# --- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anki-builder", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", help="Path to config.toml (default: ./config.toml if present)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch-dumps", help="Download Tatoeba dumps")
    p_fetch.add_argument("--force", action="store_true", help="Re-download existing dumps")
    p_fetch.set_defaults(func=cmd_fetch_dumps)

    p_build = sub.add_parser("build-db", help="Ingest dumps into SQLite")
    p_build.add_argument("--dumps", help="Dumps directory (default: config paths.dumps_dir)")
    p_build.set_defaults(func=cmd_build_db)

    p_run = sub.add_parser("run", help="Mine cards for words into the review queue")
    grp = p_run.add_mutually_exclusive_group(required=True)
    grp.add_argument("--word", help="A single word to mine")
    grp.add_argument("--words", help="Path to a file with one word per line")
    p_run.add_argument("--dry-run", action="store_true", help="Print only; don't enqueue")
    p_run.add_argument(
        "--force", action="store_true", help="Mine even if the word is already queued"
    )
    p_run.set_defaults(func=cmd_run)

    for name in ("review", "push"):
        p = sub.add_parser(name, help=f"{name} (next pass)")
        p.set_defaults(func=cmd_stub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
