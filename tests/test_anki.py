"""Anki integration (Step 4): note-type model, AnkiClient, push (mocked invoke).

No live Anki — a FakeAnki replaces the single network seam (`invoke`).
"""

from __future__ import annotations

from anki_builder.anki import model
from anki_builder.anki.connect import AnkiClient
from anki_builder.anki.push import push_accepted
from anki_builder.config import load_config
from anki_builder.db import queries

_FIELDS = {
    "Word": "comer",
    "Sentence": "Quiero comer.",
    "SentenceBlanked": "Quiero ____.",
    "Translation": "I want to eat.",
    "Audio": "[sound:tatoeba_spa_1.mp3]",
    "Source": "Tatoeba #1 · foo",
    "Flag": "",
}
_CANDS = [
    {"sentence_id": 1, "spa_text": "Quiero comer.", "translation": "I want to eat.",
     "audio_id": 5001, "username": "foo", "is_native": False},
]


class FakeAnki(AnkiClient):
    """AnkiClient whose `invoke` is fully in-memory."""

    def __init__(self, *, can_add=(True,)):
        super().__init__("http://test")
        self.calls: list[tuple[str, dict]] = []
        self.decks: list[str] = []
        self.models: list[str] = []
        self.media: list[str] = []
        self.notes: list[dict] = []
        self._can_add = list(can_add)

    def invoke(self, action, **params):
        self.calls.append((action, params))
        if action == "deckNames":
            return list(self.decks)
        if action == "modelNames":
            return list(self.models)
        if action == "createDeck":
            self.decks.append(params["deck"]); return 1
        if action == "createModel":
            self.models.append(params["modelName"]); return {}
        if action == "canAddNotes":
            return list(self._can_add)
        if action == "storeMediaFile":
            self.media.append(params["filename"]); return params["filename"]
        if action == "addNote":
            self.notes.append(params["note"]); return 12345
        raise AssertionError(f"unexpected action {action}")


def _enqueue_accepted(conn) -> int:
    rid = queries.enqueue(
        conn, word="comer", status="accepted", chosen_sentence_id=1,
        candidates=_CANDS, fields=_FIELDS, audio_filename="tatoeba_spa_1.mp3", flag="",
    )
    conn.commit()
    return rid


def _patch_audio(monkeypatch, tmp_path):
    mp3 = tmp_path / "clip.mp3"
    mp3.write_bytes(b"MP3")

    def fake_dl(sentence_id, *, lang, cache_dir, audio_id=None, force=False, client=None):
        return mp3

    monkeypatch.setattr("anki_builder.tatoeba.audio.download_audio", fake_dl)


# --- model.py --------------------------------------------------------------


def test_model_definition_shape():
    m = model.model_definition("AnkiBuilder Spanish")
    assert m["inOrderFields"] == [
        "Word", "Sentence", "SentenceBlanked", "Translation", "Audio", "Source", "Flag",
    ]
    assert m["isCloze"] is False
    names = [t["Name"] for t in m["cardTemplates"]]
    assert names == ["Recognition", "Production"]
    production = m["cardTemplates"][1]
    assert "{{type:Word}}" in production["Front"]
    assert "{{SentenceBlanked}}" in production["Front"]


def test_note_payload_carries_seven_fields_and_dedup_option():
    note = model.note_payload("Deck", "Model", _FIELDS, allow_duplicate=False)
    assert set(note["fields"]) == set(model.FIELDS)
    assert note["options"]["allowDuplicate"] is False
    assert note["deckName"] == "Deck"


# --- connect.py ------------------------------------------------------------


def test_ensure_model_is_idempotent():
    client = FakeAnki()
    defn = model.model_definition("AnkiBuilder Spanish")
    client.ensure_model(defn)
    client.ensure_model(defn)  # already present -> no second createModel
    assert [a for a, _ in client.calls].count("createModel") == 1


# --- push.py ---------------------------------------------------------------


def test_push_adds_note_stores_media_and_marks_pushed(corpus, monkeypatch, tmp_path):
    rid = _enqueue_accepted(corpus)
    _patch_audio(monkeypatch, tmp_path)
    cfg = load_config()
    client = FakeAnki()

    summary = push_accepted(corpus, cfg, dry_run=False, force=False, client=client, log=lambda *_: None)

    assert summary.pushed == 1 and summary.skipped == 0 and not summary.errors
    assert len(client.notes) == 1
    assert "tatoeba_spa_1.mp3" in client.media
    assert queries.get_row(corpus, rid)["status"] == "pushed"


def test_push_dedupe_skips_when_canaddnotes_false(corpus, monkeypatch, tmp_path):
    rid = _enqueue_accepted(corpus)
    _patch_audio(monkeypatch, tmp_path)
    cfg = load_config()
    client = FakeAnki(can_add=(False,))

    summary = push_accepted(corpus, cfg, dry_run=False, force=False, client=client, log=lambda *_: None)

    assert summary.pushed == 0 and summary.skipped == 1
    assert client.notes == []
    assert queries.get_row(corpus, rid)["status"] == "accepted"  # left for a forced retry


def test_push_force_overrides_dedupe(corpus, monkeypatch, tmp_path):
    _enqueue_accepted(corpus)
    _patch_audio(monkeypatch, tmp_path)
    cfg = load_config()
    client = FakeAnki(can_add=(False,))

    summary = push_accepted(corpus, cfg, dry_run=False, force=True, client=client, log=lambda *_: None)

    assert summary.pushed == 1
    assert client.notes[0]["options"]["allowDuplicate"] is True
    assert "canAddNotes" not in [a for a, _ in client.calls]


def test_push_dry_run_writes_nothing(corpus, monkeypatch, tmp_path):
    rid = _enqueue_accepted(corpus)
    _patch_audio(monkeypatch, tmp_path)
    cfg = load_config()
    client = FakeAnki()

    summary = push_accepted(corpus, cfg, dry_run=True, force=False, client=client, log=lambda *_: None)

    assert summary.pushed == 1
    assert client.calls == []  # never touched Anki
    assert queries.get_row(corpus, rid)["status"] == "accepted"
