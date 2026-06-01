"""review_queue enqueue + dedup helper (D11/D12 gate)."""

from __future__ import annotations

from anki_builder.db import queries


def test_enqueue_and_word_in_queue(corpus):
    assert queries.word_in_queue(corpus, "como") is False

    row_id = queries.enqueue(
        corpus,
        word="como",
        status="pending",
        chosen_sentence_id=1,
        candidates=[{"sentence_id": 1}],
        fields={"Word": "como"},
        audio_filename="tatoeba_spa_1.mp3",
        flag="",
    )
    corpus.commit()

    assert isinstance(row_id, int)
    assert queries.word_in_queue(corpus, "como") is True


def test_deleted_rows_do_not_block_requeue(corpus):
    queries.enqueue(
        corpus,
        word="gato",
        status="deleted",
        chosen_sentence_id=None,
        candidates=None,
        fields=None,
        audio_filename=None,
        flag=None,
    )
    corpus.commit()
    # A previously deleted word is mineable again.
    assert queries.word_in_queue(corpus, "gato") is False
