"""OpenAI-SDK seams for the LLM fallback (step 7) and v1.1 vision (step 8).

Mirrors the project's single-wire-seam convention (`anki.connect.AnkiClient`):
one tiny method per file does the network call (`LLMClient._chat`,
`TTSClient._speech`), so tests monkeypatch exactly that and `pytest` never needs
a key or the network. `openai` is imported lazily inside those seams.
"""
