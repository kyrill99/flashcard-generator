"""FreeDict dictionary source: TEI parse, inflection-proof gloss_for, fetch_dict.

No network: `parse_tei` reads an inline TEI string; `gloss_for` runs on the
shared fixture glossary; `fetch_dict` is driven by an httpx MockTransport serving
an in-memory `.tar.xz`, mirroring the audio tests.
"""

from __future__ import annotations

import io
import tarfile

import httpx

from anki_builder.db import queries
from anki_builder.dictionary import freedict

# A minimal TEI doc in the FreeDict shape (note the default TEI namespace).
_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
  <entry>
    <form><orth>comer</orth></form>
    <gramGrp><pos>verb</pos></gramGrp>
    <sense>
      <cit type="trans"><quote>to eat</quote></cit>
      <cit type="trans"><quote>to dine</quote></cit>
    </sense>
    <sense>
      <cit type="trans"><quote>to corrode</quote></cit>
    </sense>
  </entry>
  <entry>
    <form><orth>gato</orth></form>
    <gramGrp><gram type="pos">noun</gram></gramGrp>
    <sense><cit type="trans"><quote>cat</quote></cit></sense>
  </entry>
  <entry>
    <form><orth>sinsentido</orth></form>
    <sense></sense>
  </entry>
</body></text></TEI>
"""


# --- parse_tei -------------------------------------------------------------


def test_parse_tei(tmp_path):
    path = tmp_path / "spa-eng.tei"
    path.write_text(_TEI, encoding="utf-8")

    rows = list(freedict.parse_tei(path))

    # Only the FIRST sense's trans quotes form the gloss; entries without any
    # translation (sinsentido) are skipped.
    assert rows == [
        ("comer", "to eat, to dine", "verb"),
        ("gato", "cat", "noun"),
    ]


# --- gloss_for (the inflection fix) ----------------------------------------


def test_gloss_for_exact(corpus):
    assert queries.gloss_for(corpus, "comer") == "to eat"
    assert queries.gloss_for(corpus, "comida") == "food"  # exact wins over stem


def test_gloss_for_accent_fold(corpus):
    # Unaccented/upper query folds onto the accented headword `rápido`.
    assert queries.gloss_for(corpus, "rapido") == "fast"
    assert queries.gloss_for(corpus, "RÁPIDO") == "fast"


def test_gloss_for_stem_fallback_prefers_verb(corpus):
    # `comía` is absent as a headword → stem `com` collides with comer/comida/como;
    # the verb-preference picks comer, not the noun comida or conjunction como.
    assert queries.gloss_for(corpus, "comía") == "to eat"
    assert queries.gloss_for(corpus, "comiendo") == "to eat"


def test_gloss_for_miss_returns_empty(corpus):
    assert queries.gloss_for(corpus, "zzqwx") == ""


# --- fetch_dict (no network) -----------------------------------------------


def _tar_xz_with_tei(tei_bytes: bytes, member="freedict-spa-eng/spa-eng.tei") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(tei_bytes)
        tar.addfile(info, io.BytesIO(tei_bytes))
    return buf.getvalue()


def test_fetch_dict_extracts_tei(tmp_path):
    archive = _tar_xz_with_tei(_TEI.encode("utf-8"))
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=archive)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dest = freedict.fetch_dict(tmp_path, client=client, log=lambda *_: None)

    assert dest == freedict.dict_path(tmp_path)
    assert calls == [freedict.DICT_URL]
    # The extracted .tei is parseable end-to-end.
    assert ("gato", "cat", "noun") in list(freedict.parse_tei(dest))


def test_fetch_dict_skips_when_cached(tmp_path):
    dest = freedict.dict_path(tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_TEI, encoding="utf-8")

    def handler(request):  # pragma: no cover - must not be called on a cache hit
        raise AssertionError("network hit despite cached .tei")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert freedict.fetch_dict(tmp_path, client=client, log=lambda *_: None) == dest
