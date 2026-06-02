"""Command-line entry points (D11 resumable stages).

    anki-builder fetch-dumps         download Tatoeba dumps
    anki-builder build-db            one-time ingest into SQLite
    anki-builder run --word comer    search -> filter -> rank -> select -> enqueue
    anki-builder review              launch the local review web app (the D2 gate)
    anki-builder push [--dry-run]    push accepted review_queue rows into Anki

`run` mines into the review_queue; `review` is the mandatory human gate (D2);
`push` (or the review app's buttons) sends accepted cards to Anki via
AnkiConnect (D10/D12).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, db
from .anki import push as anki_push
from .config import Config, load_config
from .db import build as db_build
from .db import queries
from .dictionary import freedict
from .pipeline import cards, rank, search, select
from .review import server as review_server
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
    # FreeDict gloss dictionary is optional — a failure here must not block the
    # Tatoeba corpus (the gloss lookup degrades to "" / a one-field review edit).
    try:
        freedict.fetch_dict(
            cfg.paths.dumps_dir,
            target_lang=cfg.languages.target_lang,
            base_lang=cfg.languages.base_lang,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001 — optional source, log and continue
        print(f"WARNING: FreeDict dictionary fetch failed ({exc}); glosses disabled.",
              file=sys.stderr)
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
    if counts.malformed:
        print(
            f"NOTE: skipped {counts.malformed:,} malformed dump row(s) "
            "(non-integer id).",
            file=sys.stderr,
        )
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
        gloss = queries.gloss_for(conn, word)
        fields = cards.build_card_fields(
            word, chosen, target_lang=target, word_translation=gloss
        )
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
            f"    gloss: {gloss or '(none — edit in review)'}\n"
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


def cmd_review(args, cfg: Config) -> int:
    review_server.run_server(cfg, host=args.host, port=args.port)
    return 0


def cmd_push(args, cfg: Config) -> int:
    conn = db.connect(cfg.paths.db_path)
    summary = anki_push.push_accepted(
        conn, cfg, dry_run=args.dry_run, force=args.force
    )
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(
        f"\nDone — pushed: {summary.pushed}, skipped: {summary.skipped}, "
        f"errors: {len(summary.errors)}"
    )
    return 1 if summary.errors else 0


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

    p_review = sub.add_parser("review", help="Launch the local review web app (D2 gate)")
    p_review.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_review.add_argument("--port", type=int, default=8000, help="Bind port")
    p_review.set_defaults(func=cmd_review)

    p_push = sub.add_parser("push", help="Push accepted review_queue rows into Anki")
    p_push.add_argument(
        "--dry-run", action="store_true", help="Print payloads; don't touch Anki"
    )
    p_push.add_argument(
        "--force", action="store_true", help="Allow duplicates (override canAddNotes)"
    )
    p_push.set_defaults(func=cmd_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
