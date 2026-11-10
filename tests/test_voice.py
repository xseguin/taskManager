from datetime import timedelta

import pytest

from assistant.hermes.voice import TranscriptionError, VoiceFlow
from tests.fakes import FakeMessenger, FakeSubmitter


@pytest.fixture
def submitter():
    return FakeSubmitter()


@pytest.fixture
def messenger():
    return FakeMessenger()


@pytest.fixture
def flow(messenger, submitter, clock):
    return VoiceFlow(messenger, submitter, lambda audio: "acheter du pain", clock)


@pytest.fixture
def flow_with_text(messenger, submitter, clock):
    def make(text):
        return VoiceFlow(messenger, submitter, lambda audio: text, clock), messenger
    return make


@pytest.fixture
def flow_failing_once(messenger, submitter, clock):
    calls = []

    def transcribe(audio):
        calls.append(1)
        if len(calls) == 1:
            raise TranscriptionError("boom")
        return "acheter du pain"
    return VoiceFlow(messenger, submitter, transcribe, clock)


def _key(messenger):
    return messenger.sent[-1][2][0].data.split(":")[3]


WARN = "⚠️ Hermes n'a pas confirmé la réception."


async def test_voice_shows_card_with_three_buttons(flow, messenger):
    await flow.on_voice("42", b"ogg", "42:7")
    chat, text, buttons = messenger.sent[-1]
    assert text == "🎙️ « acheter du pain »"
    assert [b.data.split(":")[2] for b in buttons] == ["send", "task", "cancel"]
    assert all(b.data.startswith("ta:v:") for b in buttons)


async def test_voice_card_escapes_html(flow_with_text):
    flow, messenger = flow_with_text("a < b & c")
    await flow.on_voice("42", b"ogg", "42:7")
    assert messenger.sent[-1][1] == "🎙️ « a &lt; b &amp; c »"


async def test_send_submits_immediately_without_prior_human_turn(flow, messenger, submitter):
    # Aucun tour humain préalable (session neuve, gateway redémarrée) : envoi direct, sans « ok ».
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    assert submitter.calls == [("42", "42:7", "acheter du pain")]
    assert messenger.edits[-1][2].endswith("⏳ Transmis à Hermes…")
    assert all("ok</b>" not in e[2] for e in messenger.edits)
    await flow.on_llm_call("blabla acheter du pain")
    assert messenger.edits[-1][2].endswith("✅ Reçu par Hermes")


async def test_task_button_marks_text(flow, submitter, messenger):
    await flow.on_voice("42", b"ogg", "42:7")
    await flow.on_button("42", 1, "task", _key(messenger))
    assert submitter.calls == [("42", "42:7", "[Créer une tâche Todoist] acheter du pain")]
    await flow.on_llm_call("[Créer une tâche Todoist] acheter du pain")
    assert messenger.edits[-1][2].endswith("📝 Reçu par Hermes comme tâche")


async def test_confirmation_matches_transcript_even_when_rewritten(flow, messenger):
    # Une correction de proposition (« Modifier ») enveloppe la transcription : la réception reste confirmée.
    await flow.on_voice("42", b"ogg", "42:7")
    await flow.on_button("42", 1, "task", _key(messenger))
    await flow.on_llm_call("Je veux modifier…\nCorrection demandée : acheter du pain\n")
    assert messenger.edits[-1][2].endswith("📝 Reçu par Hermes comme tâche")


async def test_double_send_submits_once(flow, submitter, messenger):
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key); await flow.on_button("42", 1, "send", key)
    assert len(submitter.calls) == 1
    assert messenger.sent[-1][1] == "Ce vocal a expiré ou a déjà été traité."


async def test_timeout_offers_resend_button(flow, clock, messenger):
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    clock.advance(timedelta(seconds=181)); await flow.tick()
    _, _, text, buttons = messenger.edits[-1]
    assert text == f"🎙️ « acheter du pain »\n\n{WARN}"
    assert [b.data for b in buttons] == [f"ta:v:resend:{key}"]
    assert [b.text for b in buttons] == ["🔁 Renvoyer"]


async def test_resend_submits_once_more_then_no_more_button(flow, clock, messenger, submitter):
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "task", key)
    clock.advance(timedelta(seconds=181)); await flow.tick()
    await flow.on_button("42", 1, "resend", key)
    assert len(submitter.calls) == 2 and submitter.calls[1][2].startswith("[Créer une tâche Todoist]")
    assert messenger.edits[-1][2].endswith("⏳ Transmis à Hermes…")
    await flow.on_button("42", 1, "resend", key)  # double clic : un seul renvoi
    assert len(submitter.calls) == 2
    clock.advance(timedelta(seconds=181)); await flow.tick()
    assert messenger.edits[-1][2].endswith(WARN) and not messenger.edits[-1][3]
    await flow.on_button("42", 1, "resend", key)
    assert len(submitter.calls) == 2
    await flow.on_llm_call("[Créer une tâche Todoist] acheter du pain")
    assert messenger.edits[-1][2].endswith("📝 Reçu par Hermes comme tâche")


async def test_resend_before_timeout_is_refused(flow, messenger, submitter):
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    await flow.on_button("42", 1, "resend", key)
    assert len(submitter.calls) == 1
    assert messenger.sent[-1][1] == "Ce vocal a expiré ou a déjà été traité."


async def test_submit_failure_offers_resend_right_away(flow, messenger, submitter):
    submitter.fail = True
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    assert messenger.edits[-1][2].endswith(WARN)
    assert messenger.edits[-1][3][0].data == f"ta:v:resend:{key}"
    submitter.fail = False
    await flow.on_button("42", 1, "resend", key)
    assert len(submitter.calls) == 2


async def test_cancel_sends_nothing(flow, submitter, messenger):
    await flow.on_voice("42", b"ogg", "42:7"); await flow.on_button("42", 1, "cancel", _key(messenger))
    assert submitter.calls == [] and messenger.edits[-1][2].endswith("✖️ Annulé")


async def test_card_expires_after_24h(flow, clock, submitter, messenger):
    await flow.on_voice("42", b"ogg", "42:7"); key = _key(messenger)
    clock.advance(timedelta(hours=24, seconds=1)); await flow.tick()
    await flow.on_button("42", 1, "send", key)
    assert submitter.calls == []


async def test_transcription_failure_offers_retry_then_succeeds(flow_failing_once, messenger, submitter):
    flow = flow_failing_once
    await flow.on_voice("42", b"ogg", "42:7")
    text, buttons = messenger.sent[-1][1:]
    assert text == "⚠️ La transcription a échoué." and buttons[0].data.startswith("ta:v:again:")
    await flow.on_button("42", 1, "again", buttons[0].data.split(":")[3])
    assert messenger.sent[-1][1] == "🎙️ « acheter du pain »"
    await flow.on_button("42", 1, "send", _key(messenger))
    assert submitter.calls == [("42", "42:7", "acheter du pain")]  # le vocal d'origine est conservé


async def test_empty_transcript(flow_with_text):
    flow, messenger = flow_with_text("  ")
    await flow.on_voice("42", b"ogg", "42:7")
    assert messenger.sent[-1][1] == "Je n'ai pas compris, tu peux répéter ou l'écrire ?"
