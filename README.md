# Personal Anki Card Builder (Spanish, corpus-driven)

Turn target Spanish words into review-ready Anki cards built from **real Tatoeba
sentences + native-speaker audio** — a recognition card and a type-in/cloze
production card per word. The LLM is only a fallback when Tatoeba has no good
match. Every card passes a mandatory human review before it enters a deck.

> **Status:** the full `run → review → push` loop works — corpus DB, tiered
> search pipeline, card-field construction, the FastAPI review gate, and
> AnkiConnect push, all unit-tested (Anki/audio mocked). Only the live LLM/TTS
> fallback and the v1.1 vision stub are not built yet.
> See [docs/status/implementation-status.md](docs/status/implementation-status.md).

## Quickstart

```bash
uv sync                              # create .venv + install deps
uv run pytest                        # run the test suite (no network/Anki needed)

uv run anki-builder fetch-dumps      # download Tatoeba dumps (network)
uv run anki-builder build-db         # one-time ingest into SQLite
uv run anki-builder run --word comer # mine a word -> review_queue (prints a summary)
uv run anki-builder review           # review gate at http://127.0.0.1:8000 (hear/swap/edit/accept)
uv run anki-builder push             # push accepted cards -> Anki (needs Anki + AnkiConnect)
```

Copy `config.example.toml` → `config.toml` and `.env.example` → `.env` to
override defaults (deck, languages, paths, ranking weights, LLM/TTS settings).

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/README.md](docs/README.md) | Index of all project docs |
| [docs/guide/documentation.md](docs/guide/documentation.md) | Architecture, modules, CLI, config, DB schema (as built) |
| [docs/status/implementation-status.md](docs/status/implementation-status.md) | What's implemented vs. deferred, mapped to the original plan |
| [docs/testing/testing-plan.md](docs/testing/testing-plan.md) | Test coverage, how to run, and manual verification steps |
| [docs/specs/implementation_plan_v1.md](docs/specs/implementation_plan_v1.md) | Original architecture + decision log (D1–D13) |
| [docs/specs/personal_anki_builder_spec_v2.md](docs/specs/personal_anki_builder_spec_v2.md) | v1 product spec |
| [docs/specs/PRD-v1.md](docs/specs/PRD-v1.md) | Original PRD |

## Requirements

- Python ≥ 3.11, [uv](https://docs.astral.sh/uv/)
- To `push`: Anki desktop + the AnkiConnect add-on (code `2055492159`) running
- (Later pass — LLM/TTS fallback) an OpenAI-compatible API key
