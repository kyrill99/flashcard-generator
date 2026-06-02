"""Download + parse the offline FreeDict spa-eng dictionary (word glosses).

Mirrors `tatoeba/dumps.py`: `fetch_dict` streams the FreeDict `.src.tar.xz`,
extracts the bundled `.tei` member into `dumps_dir`, and `build-db` ingests it
into the `glossary` table (optional, exactly like `user_languages`). `parse_tei`
yields `(headword, gloss, pos)` rows for the ingest.

FreeDict spa-eng is licensed CC-BY-SA / GPL — https://freedict.org/.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from typing import Iterator

import httpx

# Pinned release; the `.src` archive bundles the TEI source we parse.
DICT_URL = (
    "https://download.freedict.org/dictionaries/"
    "spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"
)

# TEI namespace; ElementTree reports tags fully-qualified, so we prefix lookups.
_TEI_NS = "{http://www.tei-c.org/ns/1.0}"

_CHUNK = 1 << 16


def dict_path(dumps_dir: Path, target_lang: str = "spa", base_lang: str = "eng") -> Path:
    """Canonical local filename the downloader writes / the ingest reads."""
    return Path(dumps_dir) / f"{target_lang}-{base_lang}.tei"


def _download_tei(url: str, dest: Path, client: httpx.Client, log) -> None:
    """Stream the `.tar.xz`, extract its `.tei` member to `dest` (atomic)."""
    archive = dest.with_name(dest.name + ".tar.xz.part")
    bytes_in = 0
    with client.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with archive.open("wb") as out:
            for chunk in resp.iter_bytes(_CHUNK):
                bytes_in += len(chunk)
                out.write(chunk)
    try:
        with tarfile.open(archive, "r:xz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.name.endswith(".tei")), None
            )
            if member is None:
                raise FileNotFoundError("no .tei member in the FreeDict archive")
            src = tar.extractfile(member)
            if src is None:
                raise FileNotFoundError("could not read the .tei member")
            tmp = dest.with_name(dest.name + ".part")
            with src, tmp.open("wb") as out:
                shutil.copyfileobj(src, out)
        tmp.replace(dest)
    finally:
        archive.unlink(missing_ok=True)
    log(f"  -> {dest.name} ({bytes_in / 1e6:.1f} MB compressed)")


def fetch_dict(
    dumps_dir: str | Path,
    *,
    target_lang: str = "spa",
    base_lang: str = "eng",
    force: bool = False,
    log=print,
    client: httpx.Client | None = None,
) -> Path | None:
    """Download the FreeDict TEI into ``dumps_dir``; return its path (None on skip).

    Optional source: a cache hit (and not ``force``) short-circuits. Network/parse
    errors propagate to the caller, which logs and continues (the gloss lookup
    degrades gracefully when the `.tei` is absent).
    """
    dumps_dir = Path(dumps_dir)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    dest = dict_path(dumps_dir, target_lang, base_lang)

    if dest.exists() and not force:
        log(f"exists, skipping: {dest.name} (use --force to re-download)")
        return dest

    log(f"downloading {DICT_URL}")
    owns_client = client is None
    client = client or httpx.Client()
    try:
        _download_tei(DICT_URL, dest, client, log)
    finally:
        if owns_client:
            client.close()
    return dest


def _first_text(elem) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def parse_tei(path: str | Path) -> Iterator[tuple[str, str, str]]:
    """Yield ``(headword, gloss, pos)`` for each TEI ``entry`` with a gloss.

    headword = the entry's ``orth``; gloss = the first sense's
    ``cit[@type='trans']/quote`` texts joined with ``", "``; pos = ``gramGrp/pos``
    or ``gramGrp/gram`` (``""`` when absent). Entries lacking a headword or any
    translation are skipped. Streams with ``iterparse`` + ``clear()`` to bound
    memory on the full dictionary.
    """
    import xml.etree.ElementTree as ET

    for _event, entry in ET.iterparse(str(path), events=("end",)):
        if entry.tag != f"{_TEI_NS}entry":
            continue

        headword = _first_text(entry.find(f".//{_TEI_NS}orth"))

        gloss = ""
        sense = entry.find(f"{_TEI_NS}sense")
        if sense is not None:
            quotes = [
                _first_text(q)
                for q in sense.findall(
                    f"{_TEI_NS}cit[@type='trans']/{_TEI_NS}quote"
                )
            ]
            gloss = ", ".join(q for q in quotes if q)

        pos_elem = entry.find(f".//{_TEI_NS}gramGrp/{_TEI_NS}pos")
        if pos_elem is None:
            pos_elem = entry.find(f".//{_TEI_NS}gramGrp/{_TEI_NS}gram")
        pos = _first_text(pos_elem)

        entry.clear()
        if headword and gloss:
            yield (headword, gloss, pos)
