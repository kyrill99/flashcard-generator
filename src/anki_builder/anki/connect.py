"""Thin AnkiConnect client (D10/D12).

Talks the AnkiConnect JSON-RPC ("version 6") protocol over HTTP. `invoke` is the
*only* method that touches the wire, so tests monkeypatch it to run without a
live Anki. Everything else (ensure deck/model, store media, dedupe, add note) is
built on top of `invoke`.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

DEFAULT_URL = "http://127.0.0.1:8765"
ANKICONNECT_VERSION = 6


class AnkiConnectError(RuntimeError):
    """AnkiConnect returned an `error`, or the request could not be made."""


class AnkiClient:
    """Minimal AnkiConnect client. One network seam: :meth:`invoke`."""

    def __init__(self, url: str = DEFAULT_URL, *, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout

    def invoke(self, action: str, **params):
        """POST one AnkiConnect action and return its `result` (raises on error)."""
        payload = {"action": action, "version": ANKICONNECT_VERSION, "params": params}
        try:
            resp = httpx.post(self.url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:  # connection refused, timeout, non-2xx…
            raise AnkiConnectError(
                f"AnkiConnect request failed ({action}): {exc}. "
                "Is Anki running with the AnkiConnect add-on?"
            ) from exc
        # AnkiConnect always returns {"result", "error"}; error is non-null on failure.
        if not isinstance(data, dict) or "error" not in data or "result" not in data:
            raise AnkiConnectError(f"Unexpected AnkiConnect response: {data!r}")
        if data["error"] is not None:
            raise AnkiConnectError(f"{action}: {data['error']}")
        return data["result"]

    # --- decks / models ----------------------------------------------------

    def version(self) -> int:
        return self.invoke("version")

    def deck_names(self) -> list[str]:
        return self.invoke("deckNames")

    def ensure_deck(self, name: str) -> None:
        """Create the deck if it does not already exist (idempotent)."""
        if name not in self.deck_names():
            self.invoke("createDeck", deck=name)

    def model_names(self) -> list[str]:
        return self.invoke("modelNames")

    def ensure_model(self, model_def: dict) -> None:
        """Create the note type if absent (idempotent).

        Only *creates* — updating the templates/CSS of a model that already
        exists is out of scope; edit it in Anki or rename the model in config.
        """
        if model_def["modelName"] not in self.model_names():
            self.invoke("createModel", **model_def)

    # --- media / notes -----------------------------------------------------

    def store_media_file(self, filename: str, data_b64: str) -> str:
        """Store base64 `data_b64` under `filename` in the collection media dir."""
        return self.invoke("storeMediaFile", filename=filename, data=data_b64)

    def store_media_path(self, filename: str, path: str | Path) -> str:
        """Read a local mp3 and store it under `filename` (base64, D10)."""
        data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return self.store_media_file(filename, data)

    def can_add_notes(self, notes: list[dict]) -> list[bool]:
        return self.invoke("canAddNotes", notes=notes)

    def add_note(self, note: dict) -> int:
        return self.invoke("addNote", note=note)
