"""Download + decompress the Tatoeba dumps `build-db` needs (fetch-dumps).

Uses the per-language exports, including the **bilingual** `spa-eng_links` file
(far smaller than the global 30M-row links dump) and the per-language
`spa_user_languages`. Each `.tsv.bz2` is streamed and decompressed on the fly to
a plain `.tsv` under `dumps_dir`, named to match `db/build.dump_paths`.
"""

from __future__ import annotations

import bz2
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE_URL = "https://downloads.tatoeba.org/exports/per_language"
_CHUNK = 1 << 16


@dataclass
class DumpSpec:
    url: str
    dest: Path
    required: bool


def dump_specs(dumps_dir: Path, target_lang: str, base_lang: str) -> list[DumpSpec]:
    t, b = target_lang, base_lang
    return [
        DumpSpec(f"{BASE_URL}/{t}/{t}_sentences.tsv.bz2", dumps_dir / f"{t}_sentences.tsv", True),
        DumpSpec(f"{BASE_URL}/{b}/{b}_sentences.tsv.bz2", dumps_dir / f"{b}_sentences.tsv", True),
        DumpSpec(f"{BASE_URL}/{t}/{t}-{b}_links.tsv.bz2", dumps_dir / f"{t}-{b}_links.tsv", True),
        DumpSpec(
            f"{BASE_URL}/{t}/{t}_sentences_with_audio.tsv.bz2",
            dumps_dir / f"{t}_sentences_with_audio.tsv",
            True,
        ),
        DumpSpec(
            f"{BASE_URL}/{t}/{t}_user_languages.tsv.bz2",
            dumps_dir / f"{t}_user_languages.tsv",
            False,
        ),
    ]


def _download_bz2(url: str, dest: Path, log) -> None:
    """Stream-download a .bz2 and write its decompressed contents to `dest`."""
    decompressor = bz2.BZ2Decompressor()
    tmp = dest.with_name(dest.name + ".part")
    bytes_in = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as out:
            for chunk in resp.iter_bytes(_CHUNK):
                bytes_in += len(chunk)
                out.write(decompressor.decompress(chunk))
    tmp.replace(dest)
    log(f"  -> {dest.name} ({bytes_in / 1e6:.1f} MB compressed)")


def fetch_dumps(
    dumps_dir: str | Path,
    *,
    target_lang: str = "spa",
    base_lang: str = "eng",
    force: bool = False,
    log=print,
) -> list[Path]:
    """Download all needed dumps into ``dumps_dir``. Returns written paths."""
    dumps_dir = Path(dumps_dir)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for spec in dump_specs(dumps_dir, target_lang, base_lang):
        if spec.dest.exists() and not force:
            log(f"exists, skipping: {spec.dest.name} (use --force to re-download)")
            written.append(spec.dest)
            continue
        log(f"downloading {spec.url}")
        try:
            _download_bz2(spec.url, spec.dest, log)
            written.append(spec.dest)
        except httpx.HTTPStatusError as exc:
            if not spec.required and exc.response.status_code == 404:
                log(f"  optional dump not found (404), skipping: {spec.dest.name}")
                continue
            raise

    return written
