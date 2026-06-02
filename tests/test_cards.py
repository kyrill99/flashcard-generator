"""SentenceBlanked generation (accents/punctuation/casing) and field assembly."""

from __future__ import annotations

from anki_builder.models import Candidate
from anki_builder.pipeline.cards import BLANK, blank_sentence, build_card_fields


def test_blank_exact():
    assert blank_sentence("Yo como una manzana.", "como") == f"Yo {BLANK} una manzana."


def test_blank_preserves_accents_and_neighbours():
    # The accented target token is blanked; `sofá` (also accented) is untouched.
    assert blank_sentence("El sofá es muy cómodo.", "cómodo") == f"El sofá es muy {BLANK}."


def test_blank_is_case_insensitive():
    assert blank_sentence("Como pan.", "como") == f"{BLANK} pan."


def test_blank_enclitic_via_fold_substring():
    # `come` is inside the enclitic `cómelo`; punctuation is preserved.
    assert blank_sentence("¡Cómelo, por favor!", "come") == f"¡{BLANK}, por favor!"


def test_blank_inflection_via_stem():
    # stem(comió) == stem(comer) == 'com' -> the conjugated form is blanked.
    assert blank_sentence("Él comió pan.", "comer") == f"Él {BLANK} pan."


def test_blank_all_occurrences_at_best_level():
    # Both `Como` and `como` are exact matches -> neither leaks the answer.
    assert blank_sentence("Como como siempre.", "como") == f"{BLANK} {BLANK} siempre."


def test_no_match_returns_text_unchanged():
    assert blank_sentence("Hola mundo.", "xyz") == "Hola mundo."


def test_build_card_fields():
    candidate = Candidate(
        sentence_id=42,
        spa_text="Yo como una manzana.",
        translation="I eat an apple.",
        audio_id=5042,
        username="foo",
    )
    fields = build_card_fields("como", candidate, target_lang="spa")
    d = fields.as_dict()
    assert d["Word"] == "como"
    assert d["WordTranslation"] == ""  # defaults empty; filled by gloss_for / review
    assert d["Sentence"] == "Yo como una manzana."
    assert d["SentenceBlanked"] == f"Yo {BLANK} una manzana."
    assert d["Translation"] == "I eat an apple."
    assert d["Audio"] == "[sound:tatoeba_spa_42.mp3]"
    assert d["Source"] == "Tatoeba #42 · foo"
    assert d["Flag"] == ""


def test_build_card_fields_carries_word_translation():
    # The L1 gloss (WordTranslation) is distinct from the sentence Translation.
    candidate = Candidate(42, "Yo como una manzana.", "I eat an apple.", audio_id=5042)
    d = build_card_fields(
        "comer", candidate, target_lang="spa", word_translation="to eat"
    ).as_dict()
    assert d["WordTranslation"] == "to eat"
    assert d["Translation"] == "I eat an apple."
