"""Review web app (Step 6): queue listing, swap, edit re-blank, accept/delete, push.

A FastAPI TestClient runs against a temp-file corpus seeded by the shared
fixture; Anki and audio are mocked so no live services are needed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from anki_builder import db
from anki_builder.config import Config, PathsConfig
from anki_builder.db import queries
from anki_builder.models import Candidate
from anki_builder.pipeline import cards
from anki_builder.review.server import create_app

from conftest import seed_corpus  # shared in-memory corpus seeder

# Two candidates so the swap path has something to switch to (1 -> 6).
_CANDS = [
    {"sentence_id": 1, "spa_text": "Yo como una manzana.", "translation": "I eat an apple.",
     "audio_id": 5001, "username": "foo", "is_native": False},
    {"sentence_id": 6, "spa_text": "El gato duerme.", "translation": "The cat sleeps.",
     "audio_id": 5006, "username": "nat", "is_native": True},
]


@pytest.fixture
def app_ctx(tmp_path):
    """A seeded file DB + Config + an enqueued pending row; yields (client, cfg, rid)."""
    db_path = tmp_path / "tatoeba.db"
    conn = db.connect(db_path)
    seed_corpus(conn)
    fields = cards.build_card_fields(
        "como", Candidate(1, "Yo como una manzana.", "I eat an apple.", audio_id=5001, username="foo"),
        target_lang="spa",
    )
    rid = queries.enqueue(
        conn, word="como", status="pending", chosen_sentence_id=1,
        candidates=_CANDS, fields=fields.as_dict(), audio_filename="tatoeba_spa_1.mp3", flag="",
    )
    conn.commit()
    conn.close()

    cfg = Config(paths=PathsConfig(
        db_path=db_path, dumps_dir=tmp_path / "dumps", media_cache=tmp_path / "media"
    ))
    # Exercise the production guard (Host allowlist + CSRF); a loopback base_url
    # makes the test client's Host pass.
    app = create_app(cfg, allowed_hosts={"127.0.0.1", "localhost", "::1"})
    yield TestClient(app, base_url="http://localhost"), cfg, rid


def test_queue_lists_pending(app_ctx):
    client, _cfg, rid = app_ctx
    rows = client.get("/api/queue?status=pending").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["fields"]["Word"] == "como"
    assert "WordTranslation" in rows[0]["fields"]  # the L1 gloss field is exposed
    assert len(rows[0]["candidates"]) == 2


def test_swap_repopulates_gloss_when_absent(app_ctx):
    # The fixture row has no gloss, so swap looks it up from the dictionary.
    client, _cfg, rid = app_ctx
    row = client.post(f"/api/queue/{rid}/swap", json={"candidate_index": 1}).json()
    assert row["fields"]["WordTranslation"] == "as, like"  # gloss_for("como")


def test_swap_preserves_edited_gloss(app_ctx):
    # A human-edited gloss must survive a candidate swap (not be overwritten).
    client, _cfg, rid = app_ctx
    client.post(f"/api/queue/{rid}/edit", json={"fields": {"WordTranslation": "my gloss"}})
    row = client.post(f"/api/queue/{rid}/swap", json={"candidate_index": 1}).json()
    assert row["fields"]["WordTranslation"] == "my gloss"


def test_edit_sets_gloss(app_ctx):
    client, _cfg, rid = app_ctx
    row = client.post(
        f"/api/queue/{rid}/edit", json={"fields": {"WordTranslation": "to eat"}}
    ).json()
    assert row["fields"]["WordTranslation"] == "to eat"


def test_swap_rebuilds_fields_and_audio(app_ctx):
    client, _cfg, rid = app_ctx
    row = client.post(f"/api/queue/{rid}/swap", json={"candidate_index": 1}).json()
    assert row["chosen_sentence_id"] == 6
    assert row["fields"]["Sentence"] == "El gato duerme."
    assert row["fields"]["Translation"] == "The cat sleeps."
    assert row["audio_filename"] == "tatoeba_spa_6.mp3"


def test_edit_reblanks_sentence(app_ctx):
    client, _cfg, rid = app_ctx
    row = client.post(
        f"/api/queue/{rid}/edit",
        json={"fields": {"Word": "gato", "Sentence": "El gato duerme."}},
    ).json()
    # SentenceBlanked is recomputed from the edited Word/Sentence, not trusted.
    assert row["fields"]["SentenceBlanked"] == "El ____ duerme."


def test_accept_then_delete_change_status(app_ctx):
    client, _cfg, rid = app_ctx
    client.post(f"/api/queue/{rid}/accept")
    assert client.get("/api/queue?status=accepted").json()["rows"][0]["id"] == rid

    client.post(f"/api/queue/{rid}/delete")
    assert client.get("/api/queue?status=pending").json()["rows"] == []


def test_push_endpoint_uses_mocked_anki(app_ctx, monkeypatch, tmp_path):
    client, _cfg, rid = app_ctx
    client.post(f"/api/queue/{rid}/accept")

    mp3 = tmp_path / "clip.mp3"
    mp3.write_bytes(b"MP3")
    monkeypatch.setattr(
        "anki_builder.tatoeba.audio.download_audio",
        lambda *a, **k: mp3,
    )

    def fake_invoke(self, action, **params):
        return {
            "deckNames": [], "modelNames": [], "createDeck": 1, "createModel": {},
            "canAddNotes": [True], "storeMediaFile": "f.mp3", "addNote": 1,
        }[action]

    monkeypatch.setattr("anki_builder.anki.connect.AnkiClient.invoke", fake_invoke)

    resp = client.post("/api/push-all")
    assert resp.status_code == 200
    assert resp.json()["pushed"] == 1
    assert queries.get_row(db.connect(_cfg.paths.db_path), rid)["status"] == "pushed"


def test_index_page_served(app_ctx):
    client, _cfg, _rid = app_ctx
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Review queue" in resp.text


# --- guard / status-machine hardening (M1/M2) ------------------------------


def test_guard_rejects_unknown_host(app_ctx):
    """Anti-DNS-rebinding: a non-loopback Host header is refused (M1)."""
    _client, cfg, rid = app_ctx
    app = create_app(cfg, allowed_hosts={"127.0.0.1", "localhost", "::1"})
    bad = TestClient(app, base_url="http://evil.example")
    assert bad.post(f"/api/queue/{rid}/accept").status_code == 400


def test_guard_blocks_cross_site_post(app_ctx):
    """CSRF: a cross-site state-changing request is refused; same-site is fine (M1)."""
    client, _cfg, rid = app_ctx
    blocked = client.post(
        f"/api/queue/{rid}/accept", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert blocked.status_code == 403
    allowed = client.post(
        f"/api/queue/{rid}/accept", headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert allowed.status_code == 200


def test_safe_get_allowed_cross_site(app_ctx):
    """The CSRF check only gates unsafe methods — GET stays usable (M1)."""
    client, _cfg, _rid = app_ctx
    resp = client.get("/api/queue", headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 200


def test_push_one_rejects_terminal_status(app_ctx):
    """A deleted row can't be re-pushed via the per-row endpoint (M2)."""
    client, _cfg, rid = app_ctx
    client.post(f"/api/queue/{rid}/delete")
    assert client.post(f"/api/queue/{rid}/push").status_code == 409


def test_push_one_promotes_pending(app_ctx, monkeypatch, tmp_path):
    """One-click Push on a pending row counts as the human accept, then pushes (M2)."""
    client, _cfg, rid = app_ctx
    mp3 = tmp_path / "clip.mp3"
    mp3.write_bytes(b"MP3")
    monkeypatch.setattr(
        "anki_builder.tatoeba.audio.download_audio", lambda *a, **k: mp3
    )
    monkeypatch.setattr(
        "anki_builder.anki.connect.AnkiClient.invoke",
        lambda self, action, **p: {
            "deckNames": [], "modelNames": [], "createDeck": 1, "createModel": {},
            "canAddNotes": [True], "storeMediaFile": "f.mp3", "addNote": 1,
        }[action],
    )
    resp = client.post(f"/api/queue/{rid}/push")
    assert resp.status_code == 200 and resp.json()["pushed"] == 1
    assert queries.get_row(db.connect(_cfg.paths.db_path), rid)["status"] == "pushed"
