from unittest.mock import MagicMock

import pytest

from assistant.transcriber import GroqTranscriber, TranscriptionError


def test_groq_called_in_french():
    c = MagicMock()
    c.audio.transcriptions.create.return_value.text = "  bonjour "
    assert GroqTranscriber(c).transcribe(b"a", "v.ogg") == "bonjour"
    kw = c.audio.transcriptions.create.call_args.kwargs
    assert kw["language"] == "fr" and kw["model"] == "whisper-large-v3-turbo"
    assert kw["file"] == ("v.ogg", b"a")


def test_retries_once_then_raises():
    c = MagicMock()
    c.audio.transcriptions.create.side_effect = RuntimeError("down")
    with pytest.raises(TranscriptionError):
        GroqTranscriber(c).transcribe(b"a", "v.ogg")
    assert c.audio.transcriptions.create.call_count == 2


def test_retry_succeeds_second_time():
    c = MagicMock()
    ok = MagicMock(text="salut")
    c.audio.transcriptions.create.side_effect = [RuntimeError("down"), ok]
    assert GroqTranscriber(c).transcribe(b"a", "v.ogg") == "salut"
