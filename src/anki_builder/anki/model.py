"""The custom note type (D3): 7 fields, two card templates, CSS.

One note type emits two cards from a single record (Anki's native Cloze type
cannot do this): a **Recognition** card (word -> meaning) and a true type-in
**Production** card (`{{type:Word}}` against the blanked sentence). The
`{{#Flag}}…{{/Flag}}` section renders a visible badge only on fallback cards.
"""

from __future__ import annotations

# Field order must match models.CardFields.as_dict() keys (D3).
FIELDS = [
    "Word",
    "Sentence",
    "SentenceBlanked",
    "Translation",
    "Audio",
    "Source",
    "Flag",
]

_FLAG_BADGE = (
    '{{#Flag}}<div class="flag">⚠ {{Flag}}</div>{{/Flag}}'
)

# Card 1 — Recognition: see the word, recall meaning + a real example + audio.
_RECOGNITION_FRONT = '<div class="word">{{Word}}</div>'
_RECOGNITION_BACK = (
    "{{FrontSide}}\n<hr id=answer>\n"
    '<div class="translation">{{Translation}}</div>\n'
    '<div class="sentence">{{Sentence}}</div>\n'
    "<div>{{Audio}}</div>\n" + _FLAG_BADGE
)

# Card 2 — Production: type the target word into the blanked sentence.
_PRODUCTION_FRONT = (
    '<div class="sentence">{{SentenceBlanked}}</div>\n{{type:Word}}'
)
_PRODUCTION_BACK = (
    "{{FrontSide}}\n<hr id=answer>\n"
    '<div class="sentence">{{Sentence}}</div>\n'
    '<div class="translation">{{Translation}}</div>\n'
    "<div>{{Audio}}</div>\n" + _FLAG_BADGE
)

_CSS = """\
.card {
  font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  font-size: 22px;
  text-align: center;
  color: #1d1d1f;
  background: #fff;
}
.word { font-size: 30px; font-weight: 600; }
.sentence { margin: 12px 0; }
.translation { color: #444; }
.flag {
  margin-top: 14px;
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  background: #ffefc2;
  color: #7a5b00;
  font-size: 14px;
  text-transform: uppercase;
}
"""


def model_definition(name: str) -> dict:
    """Build the `createModel` payload for the D3 note type."""
    return {
        "modelName": name,
        "inOrderFields": list(FIELDS),
        "isCloze": False,
        "css": _CSS,
        "cardTemplates": [
            {
                "Name": "Recognition",
                "Front": _RECOGNITION_FRONT,
                "Back": _RECOGNITION_BACK,
            },
            {
                "Name": "Production",
                "Front": _PRODUCTION_FRONT,
                "Back": _PRODUCTION_BACK,
            },
        ],
    }


def note_payload(
    deck: str,
    model_name: str,
    fields: dict,
    *,
    allow_duplicate: bool = False,
    tags: list[str] | None = None,
) -> dict:
    """Build an `addNote`/`canAddNotes` note object from card fields.

    `fields` is CardFields.as_dict() — its keys already match :data:`FIELDS`, and
    the `Audio` value carries the `[sound:…]` tag, so media is stored separately
    (storeMediaFile) and the note just references it.
    """
    note_tags = list(tags) if tags else []
    if fields.get("Flag"):
        note_tags.append(fields["Flag"])
    return {
        "deckName": deck,
        "modelName": model_name,
        "fields": {k: fields.get(k, "") for k in FIELDS},
        "tags": note_tags,
        "options": {"allowDuplicate": allow_duplicate},
    }
