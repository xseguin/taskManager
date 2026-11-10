from datetime import timedelta

import pytest

from assistant.hermes.relay import Relay
from assistant.hermes.voice import TranscriptionError, VoiceFlow
from tests.fakes import FakeInjector, FakeMessenger


@pytest.fixture
def injector():
    return FakeInjector()


@pytest.fixture
def relay(injector):
    return Relay(("42",), injector)


@pytest.fixture
def messenger():
    return FakeMessenger()


@pytest.fixture
def flow(messenger, relay, clock):
    return VoiceFlow(messenger, relay, lambda audio: "acheter du pain", clock)


@pytest.fixture
def flow_with_text(messenger, relay, clock):
    def make(text):
        return VoiceFlow(messenger, relay, lambda audio: text, clock), messenger
    return make


@pytest.fixture
def flow_failing_once(messenger, relay, clock):
    calls = []

    def transcribe(audio):
        calls.append(1)
        if len(calls) == 1:
            raise TranscriptionError("boom")
        return "acheter du pain"
    return VoiceFlow(messenger, relay, transcribe, clock)


def _key(messenger):
    return messenger.sent[-1][2][0].data.split(":")[3]


async def test_voice_shows_card_with_three_buttons(flow, messenger):
    await flow.on_voice("42", b"ogg")
    chat, text, buttons = messenger.sent[-1]
    assert text == "🎙️ « acheter du pain »"
    assert [b.data.split(":")[2] for b in buttons] == ["send", "task", "cancel"]
    assert all(b.data.startswith("ta:v:") for b in buttons)


async def test_voice_card_escapes_html(flow_with_text):
    flow, messenger = flow_with_text("a < b & c")
    await flow.on_voice("42", b"ogg")
    assert messenger.sent[-1][1] == "🎙️ « a &lt; b &amp; c »"


async def test_send_injects_and_waits_for_confirmation(flow, messenger, relay, injector):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    assert injector.calls == [("42", "acheter du pain")]
    assert messenger.edits[-1][2].endswith("⏳ Transmis à Hermes…")
    await flow.on_llm_call("blabla acheter du pain")
    assert messenger.edits[-1][2].endswith("✅ Reçu par Hermes")


async def test_task_button_marks_text(flow, relay, injector, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg")
    await flow.on_button("42", 1, "task", _key(messenger))
    assert injector.calls == [("42", "[Créer une tâche Todoist] acheter du pain")]
    await flow.on_llm_call("[Créer une tâche Todoist] acheter du pain")
    assert messenger.edits[-1][2].endswith("📝 Reçu par Hermes comme tâche")


async def test_double_send_injects_once(flow, relay, injector, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key); await flow.on_button("42", 1, "send", key)
    assert len(injector.calls) == 1
    assert messenger.sent[-1][1] == "Ce vocal a expiré ou a déjà été traité."


async def test_new_session_uses_ok_fallback(flow, relay, injector, messenger):
    await flow.on_voice("42", b"ogg"); await flow.on_button("42", 1, "send", _key(messenger))
    assert injector.calls == []
    assert "Réponds <b>ok</b>" in messenger.edits[-1][2]
    assert relay.rewrite("42", "42", "ok") == "acheter du pain"


async def test_unconfirmed_after_timeout_arms_fallback(flow, relay, clock, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg")
    await flow.on_button("42", 1, "send", _key(messenger))
    clock.advance(timedelta(seconds=181)); await flow.tick()
    assert "n'a pas confirmé" in messenger.edits[-1][2]
    assert relay.rewrite("42", "42", "ok") == "acheter du pain"


async def test_cancel_sends_nothing(flow, injector, messenger):
    await flow.on_voice("42", b"ogg"); await flow.on_button("42", 1, "cancel", _key(messenger))
    assert injector.calls == [] and messenger.edits[-1][2].endswith("✖️ Annulé")


async def test_card_expires_after_24h(flow, clock, injector, messenger):
    await flow.on_voice("42", b"ogg"); key = _key(messenger)
    clock.advance(timedelta(hours=24, seconds=1)); await flow.tick()
    await flow.on_button("42", 1, "send", key)
    assert injector.calls == []


async def test_transcription_failure_offers_retry_then_succeeds(flow_failing_once, messenger):
    flow = flow_failing_once
    await flow.on_voice("42", b"ogg")
    text, buttons = messenger.sent[-1][1:]
    assert text == "⚠️ La transcription a échoué." and buttons[0].data.startswith("ta:v:again:")
    await flow.on_button("42", 1, "again", buttons[0].data.split(":")[3])
    assert messenger.sent[-1][1] == "🎙️ « acheter du pain »"


async def test_empty_transcript(flow_with_text):
    flow, messenger = flow_with_text("  ")
    await flow.on_voice("42", b"ogg")
    assert messenger.sent[-1][1] == "Je n'ai pas compris, tu peux répéter ou l'écrire ?"
