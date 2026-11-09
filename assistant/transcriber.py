"""Transcription audio → texte."""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class TranscriptionError(Exception):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, filename: str) -> str: ...


class GroqTranscriber:
    def __init__(self, client, model: str = "whisper-large-v3-turbo"):
        self._client = client
        self._model = model

    def transcribe(self, audio: bytes, filename: str) -> str:
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                r = self._client.audio.transcriptions.create(
                    file=(filename, audio), model=self._model, language="fr")
                return (r.text or "").strip()
            except Exception as e:  # erreurs réseau/API du SDK Groq
                last = e
                log.warning("transcription échouée (tentative %d) : %s", attempt, e)
        raise TranscriptionError(str(last)) from last
