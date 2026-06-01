"""Personal Anki Card Builder — Spanish, corpus-driven from Tatoeba.

See docs/specs/implementation_plan_v1.md for the architecture and decision log
(D1..D13), and docs/status/implementation-status.md for what is built so far.
This foundation pass covers config, the SQLite corpus DB, the tiered local
search pipeline, and card-field construction. Anki/LLM/review live in a later
pass.
"""

__version__ = "0.1.0"
