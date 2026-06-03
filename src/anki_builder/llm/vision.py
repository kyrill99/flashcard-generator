"""v1.1 image input: extract target-language words from an image (step 8).

A thin wrapper so the CLI imports one symbol. It reads the file, sniffs the mime
type from the suffix, base64-encodes it, and delegates to `LLMClient.extract_words`
(the vision wire seam). The returned word strings feed the *unchanged* per-word
pipeline — "nothing downstream changes" (spec).
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..config import Config
from .client import LLMClient

# Suffix → mime for the data-URL the vision call sends. Defaults to jpeg.
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _mime_for(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")


def extract_words(
    image_path: str | Path, *, cfg: Config, client: LLMClient | None = None
) -> list[str]:
    """Return the target-language words an image contains (lemmas).

    Raises `FileNotFoundError` if the image is missing and `FallbackError` if the
    model returns an unusable response — `cmd_run` surfaces either as a clean
    non-zero CLI error rather than a traceback.
    """
    path = Path(image_path)
    data = path.read_bytes()  # FileNotFoundError propagates to the CLI
    image_b64 = base64.b64encode(data).decode("ascii")
    client = client or LLMClient(cfg.llm)
    return client.extract_words(image_b64, _mime_for(path))
