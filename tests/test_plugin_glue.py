import json
from types import SimpleNamespace

import pytest

from hermes_plugin import parse_callback, register


class FakeCtx:
    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, list] = {}
        self.platform_handlers: dict[str, object] = {}
        self.prompt_sections: dict[str, str] = {}
        self.injected: list[tuple[str, str, str]] = []
        self.inject_result = True

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler,
                            "is_async": is_async}

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)

    def register_platform_handler(self, platform, factory):
        self.platform_handlers[platform] = factory

    def register_system_prompt_section(self, id, content, *, position="after_memory",
                                       max_chars=None):
        self.prompt_sections[id] = content

    def inject_message(self, content, role="user", *, session_key=None):
        self.injected.append((content, role, session_key))
        return self.inject_result


@pytest.fixture
def fake_ctx():
    return FakeCtx()


@pytest.fixture
def token_path(monkeypatch, tmp_path):
    import hermes_plugin
    p = tmp_path / "cfg" / "token"
    p.parent.mkdir()
    p.write_text("t\n")
    monkeypatch.setattr(hermes_plugin, "_token_file", lambda: p)
    return p


@pytest.fixture
def registered(fake_ctx, monkeypatch, tmp_path, token_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register(fake_ctx)
    return fake_ctx


@pytest.mark.parametrize("data,expected", [
    ("ta:v:send:abc", ("v", "send", "abc")), ("ta:p:ok:12ab", ("p", "ok", "12ab")),
    ("cl:1:2", None), ("ta:v:send", None), ("ta:x:ok:1", None)])
def test_parse_callback(data, expected):
    assert parse_callback(data) == expected


def test_register_wires_everything(fake_ctx, monkeypatch, tmp_path, token_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register(fake_ctx)
    assert set(fake_ctx.tools) == {"todoist_list_projects", "todoist_search_tasks",
                                   "todoist_get_task", "todoist_propose_actions"}
    assert set(fake_ctx.hooks) == {"pre_gateway_dispatch", "pre_llm_call"}
    assert fake_ctx.platform_handlers.keys() == {"telegram"}
    assert "todoist-assistant.rules" in fake_ctx.prompt_sections


def test_tools_use_todoist_toolset_and_propose_is_async(registered):
    assert {t["toolset"] for t in registered.tools.values()} == {"todoist"}
    assert registered.tools["todoist_propose_actions"]["is_async"] is True
    assert not registered.tools["todoist_list_projects"]["is_async"]


async def test_propose_without_telegram_returns_error(registered):
    handler = registered.tools["todoist_propose_actions"]["handler"]
    out = json.loads(await handler({"actions": [{"kind": "create", "content": "x"}]}))
    assert "Telegram n'est pas connecté" in out["error"]


def test_read_tool_never_raises(registered, monkeypatch):
    import hermes_plugin

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(hermes_plugin, "run_read_tool", boom)
    out = json.loads(registered.tools["todoist_list_projects"]["handler"]({}))
    assert "kaboom" in out["error"]


def _event(text, user_id="42", platform="telegram", message_id="7", message_type="text",
           chat_type="dm"):
    return SimpleNamespace(text=text, message_id=message_id,
                           message_type=SimpleNamespace(value=message_type),
                           source=SimpleNamespace(platform=SimpleNamespace(value=platform), chat_id="42",
                                                  user_id=user_id, chat_type=chat_type))


def test_pre_gateway_dispatch_rewrites_t_command(registered):
    hook = registered.hooks["pre_gateway_dispatch"][0]
    res = hook(event=_event("/t acheter du pain"), gateway=None, session_store=None)
    assert res == {"action": "rewrite", "text": "[Créer une tâche Todoist] acheter du pain"}
    assert hook(event=_event("/t pain", user_id="99"), gateway=None, session_store=None) is None
    assert hook(event=_event("/t pain", platform="discord"), gateway=None, session_store=None) is None


def test_pre_llm_call_without_loop_does_not_raise(registered):
    hook = registered.hooks["pre_llm_call"][0]
    assert hook(session_id="s", user_message="x", conversation_history=[], is_first_turn=True,
                model="m", platform="telegram") is None


def _msg(chat_id, chat_type="private"):
    return SimpleNamespace(chat_id=chat_id, message_id=5,
                           chat=SimpleNamespace(id=chat_id, type=chat_type))


async def test_callback_from_unknown_user_is_ignored(registered, monkeypatch):
    import hermes_plugin
    called = []

    async def on_button(*a):
        called.append(a)
    plugin = hermes_plugin._STATE
    monkeypatch.setattr(plugin.voice, "on_button", on_button)
    answered = []

    async def answer():
        answered.append(True)
    query = SimpleNamespace(data="ta:v:send:abc", answer=answer,
                            from_user=SimpleNamespace(id=99),
                            message=_msg(99))
    await plugin.on_callback(SimpleNamespace(callback_query=query), None)
    assert answered and not called
    query.from_user = SimpleNamespace(id=42)
    query.message = _msg(42)
    await plugin.on_callback(SimpleNamespace(callback_query=query), None)
    assert called == [("42", 5, "send", "abc")]


def test_transcribe_turns_any_failure_into_transcription_error(monkeypatch):
    import os
    import sys
    import types

    import hermes_plugin
    from assistant.hermes.voice import TranscriptionError
    seen = []

    def fake(path, model, source):
        seen.append(path)
        assert os.path.exists(path) and path.endswith(".ogg")
        return {"success": False, "error": "quota"}
    mod = types.ModuleType("tools.transcription_tools")
    mod.transcribe_audio = fake
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.transcription_tools", mod)
    with pytest.raises(TranscriptionError, match="quota"):
        hermes_plugin._transcribe(b"x")
    assert not os.path.exists(seen[0])

    mod.transcribe_audio = lambda *a: (_ for _ in ()).throw(OSError("disk"))
    with pytest.raises(TranscriptionError, match="disk"):
        hermes_plugin._transcribe(b"x")
    mod.transcribe_audio = lambda *a: {"success": True, "transcript": "bonjour"}
    assert hermes_plugin._transcribe(b"x") == "bonjour"


def test_transcribe_without_hermes_raises_transcription_error(monkeypatch):
    import sys

    import hermes_plugin
    from assistant.hermes.voice import TranscriptionError
    monkeypatch.setitem(sys.modules, "tools.transcription_tools", None)
    with pytest.raises(TranscriptionError):
        hermes_plugin._transcribe(b"x")


async def test_tick_loop_survives_messenger_errors(registered, monkeypatch):
    import asyncio

    import hermes_plugin
    plugin = hermes_plugin._STATE
    calls = []

    async def tick():
        calls.append(1)
        raise RuntimeError("telegram down")
    monkeypatch.setattr(plugin.voice, "tick", tick)
    monkeypatch.setattr(hermes_plugin, "TICK_SECONDS", 0)
    task = asyncio.create_task(plugin._tick_loop())
    while len(calls) < 3:
        await asyncio.sleep(0)
    task.cancel()
    assert len(calls) >= 3


class FakeApplication:
    def __init__(self):
        self.bot = None
        self.handlers = []
        self.processed = []

    def add_handler(self, handler, group=0):
        self.handlers.append(handler)

    async def process_update(self, update):
        self.processed.append(update)


@pytest.fixture
async def wired(registered):
    import hermes_plugin
    plugin = hermes_plugin._STATE
    app = FakeApplication()
    registered.platform_handlers["telegram"](app, None)
    yield plugin, app
    if plugin._tick_task is not None:
        plugin._tick_task.cancel()


def _handlers(app):
    from telegram.ext import CallbackQueryHandler, MessageHandler
    voice = [h for h in app.handlers if isinstance(h, MessageHandler)]
    cb = [h for h in app.handlers if isinstance(h, CallbackQueryHandler)]
    assert len(voice) == 1 and len(cb) == 1
    return voice[0], cb[0]


def _tg_voice_update(chat_type):
    from datetime import datetime, timezone

    from telegram import Chat, Message, Update, User, Voice
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(42, chat_type),
                  from_user=User(42, "x", False),
                  voice=Voice(file_id="f", file_unique_id="u", duration=1))
    return Update(1, message=msg)


async def test_wire_registers_private_voice_and_ta_callback_handlers(wired):
    plugin, app = wired
    voice, cb = _handlers(app)
    assert plugin.application is app and plugin.loop is not None
    assert voice.check_update(_tg_voice_update("private"))
    assert not voice.check_update(_tg_voice_update("group"))
    assert cb.pattern.pattern == r"^ta:"


def _voice_update(user_id, fail=False):
    async def get_file():
        if fail:
            raise RuntimeError("download failed")
        async def download_as_bytearray():
            return bytearray(b"audio")
        return SimpleNamespace(download_as_bytearray=download_as_bytearray)
    msg = SimpleNamespace(chat_id=user_id, message_id=7, voice=SimpleNamespace(get_file=get_file),
                          audio=None)
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=msg)


@pytest.mark.parametrize("fail", [False, True])
async def test_authorized_voice_is_processed_and_stops_core(wired, monkeypatch, fail):
    from telegram.ext import ApplicationHandlerStop
    plugin, app = wired
    seen = []

    async def on_voice(chat_id, audio, source):
        seen.append((chat_id, audio, source))
    monkeypatch.setattr(plugin.voice, "on_voice", on_voice)
    voice, _ = _handlers(app)
    with pytest.raises(ApplicationHandlerStop):
        await voice.callback(_voice_update(42, fail=fail), None)
    assert seen == ([] if fail else [("42", b"audio", "42:7")])


async def test_unauthorized_voice_is_left_to_hermes(wired, monkeypatch):
    plugin, app = wired
    seen = []

    async def on_voice(chat_id, audio, source):
        seen.append(chat_id)
    monkeypatch.setattr(plugin.voice, "on_voice", on_voice)
    voice, _ = _handlers(app)
    await voice.callback(_voice_update(99), None)  # ne lève pas ApplicationHandlerStop
    assert seen == []


async def test_callback_outside_private_chat_is_not_dispatched(wired, monkeypatch):
    from telegram.ext import ApplicationHandlerStop
    plugin, app = wired
    called = []

    async def on_button(*a):
        called.append(a)
    monkeypatch.setattr(plugin.voice, "on_button", on_button)
    answered = []

    async def answer():
        answered.append(True)
    _, cb = _handlers(app)
    query = SimpleNamespace(data="ta:v:send:abc", answer=answer, from_user=SimpleNamespace(id=42),
                            message=_msg(-100, "group"))
    with pytest.raises(ApplicationHandlerStop):
        await cb.callback(SimpleNamespace(callback_query=query), None)
    assert answered and not called


def test_token_file_is_under_running_users_home(monkeypatch):
    import pwd
    import hermes_plugin
    monkeypatch.setenv("HOME", "/nonexistent-home")
    home = pwd.getpwuid(__import__("os").getuid()).pw_dir
    assert hermes_plugin._token_file() == hermes_plugin.Path(home) / ".config" / "todoist-assistant" / "token"


def test_register_reads_token_file_and_scrubs_env(fake_ctx, monkeypatch, tmp_path, token_path):
    import os
    import hermes_plugin
    monkeypatch.setenv("TODOIST_API_TOKEN", "from-env")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register(fake_ctx)
    assert hermes_plugin._STATE.cfg.todoist_token == "t"
    assert "TODOIST_API_TOKEN" not in os.environ
    assert "t" not in [v for k, v in os.environ.items() if "TODOIST" in k.upper()]


# --- vocal validé → message texte synthétique ------------------------------------------------


def _tg_voice_message(**extra):
    from datetime import datetime, timezone

    from telegram import Chat, Message, User, Voice
    return Message(message_id=77, date=datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
                   chat=Chat(42, "private"), from_user=User(42, "Xavier", False),
                   voice=Voice(file_id="f", file_unique_id="u", duration=3), **extra)


def test_synthetic_update_is_a_plain_text_message_from_the_user():
    from telegram import MessageEntity
    from telegram.ext import filters

    import hermes_plugin
    voice_msg = _tg_voice_message(caption="légende",
                                  caption_entities=[MessageEntity("bold", 0, 3)])
    upd = hermes_plugin.build_text_update(voice_msg, "acheter du pain", -123, None)
    m = upd.message
    assert upd.update_id == -123
    assert m.text == "acheter du pain"
    assert m.voice is None and m.audio is None and m.caption is None and not m.caption_entities
    assert not m.entities
    assert m.chat.id == 42 and m.chat.type == "private"
    assert m.from_user.id == 42 and not m.from_user.is_bot
    assert m.message_id == 77 and m.date == voice_msg.date
    # Le handler texte du cœur (filters.TEXT & ~COMMAND) le prend ; le handler vocal du plugin non.
    assert (filters.TEXT & ~filters.COMMAND).check_update(upd)
    assert not (filters.VOICE | filters.AUDIO).check_update(upd)


def test_synthetic_update_starting_with_slash_is_not_a_command():
    from telegram.ext import filters

    import hermes_plugin
    upd = hermes_plugin.build_text_update(_tg_voice_message(), "/t acheter du pain", -1, None)
    assert not filters.COMMAND.check_update(upd)
    assert (filters.TEXT & ~filters.COMMAND).check_update(upd)


def test_synthetic_update_ids_are_negative_and_unique():
    import hermes_plugin
    ids = [hermes_plugin._next_synthetic_update_id() for _ in range(1000)]
    assert all(i < 0 for i in ids)
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids, reverse=True)


async def test_submitter_feeds_synthetic_text_update_to_the_application(wired):
    plugin, app = wired
    source = plugin.submitter.remember(_tg_voice_message())
    assert source == "42:77"
    await plugin.submitter.submit("42", source, "[Créer une tâche Todoist] pain")
    (upd,) = app.processed
    assert upd.update_id < 0 and upd.message.text == "[Créer une tâche Todoist] pain"
    assert upd.message.voice is None and upd.message.message_id == 77
    assert plugin.is_synthetic("42", "77")


async def test_submitter_unknown_source_raises(wired):
    plugin, app = wired
    with pytest.raises(RuntimeError):
        await plugin.submitter.submit("42", "42:999", "pain")
    assert app.processed == []


async def test_submitter_never_feeds_another_chat(wired):
    plugin, app = wired
    source = plugin.submitter.remember(_tg_voice_message())
    with pytest.raises(RuntimeError):
        await plugin.submitter.submit("99", source, "pain")
    assert app.processed == []


def test_synthetic_message_is_not_read_as_slash_t(registered):
    import hermes_plugin
    plugin = hermes_plugin._STATE
    plugin.mark_synthetic("42", "7")
    hook = registered.hooks["pre_gateway_dispatch"][0]
    assert hook(event=_event("/t pain", message_id="7"), gateway=None, session_store=None) is None
    res = hook(event=_event("/t pain", message_id="8"), gateway=None, session_store=None)
    assert res == {"action": "rewrite", "text": "[Créer une tâche Todoist] pain"}


def test_synthetic_message_becomes_the_armed_correction(registered):
    import hermes_plugin
    plugin = hermes_plugin._STATE
    plugin.relay.arm_edit("42", "p1", "📝 Créer : pain")
    plugin.mark_synthetic("42", "7")
    hook = registered.hooks["pre_gateway_dispatch"][0]
    res = hook(event=_event("[Créer une tâche Todoist] plutôt samedi", message_id="7"),
               gateway=None, session_store=None)
    assert res["action"] == "rewrite"
    assert "p1" in res["text"] and "Correction demandée : plutôt samedi" in res["text"]


@pytest.mark.parametrize("kind", ["voice", "audio"])
def test_unvalidated_voice_from_owner_is_skipped(registered, kind):
    hook = registered.hooks["pre_gateway_dispatch"][0]
    assert hook(event=_event("", message_type=kind), gateway=None, session_store=None) == {
        "action": "skip", "reason": "todoist-assistant: vocal non validé"}
    assert hook(event=_event("", message_type=kind, user_id="99"), gateway=None,
                session_store=None) is None
    assert hook(event=_event("", message_type=kind, chat_type="group"), gateway=None,
                session_store=None) is None


def test_synthetic_text_is_not_skipped(registered):
    import hermes_plugin
    hermes_plugin._STATE.mark_synthetic("42", "7")
    hook = registered.hooks["pre_gateway_dispatch"][0]
    assert hook(event=_event("acheter du pain", message_id="7"), gateway=None,
                session_store=None) is None


async def test_pre_gateway_dispatch_captures_the_gateway_loop(registered):
    import asyncio

    import hermes_plugin
    plugin = hermes_plugin._STATE
    assert plugin.loop is None
    hook = registered.hooks["pre_gateway_dispatch"][0]
    hook(event=_event("bonjour"), gateway=None, session_store=None)
    try:
        assert plugin.loop is asyncio.get_running_loop()
    finally:
        plugin._tick_task.cancel()


def test_pre_gateway_dispatch_without_loop_does_not_capture(registered):
    import hermes_plugin
    hook = registered.hooks["pre_gateway_dispatch"][0]
    assert hook(event=_event("bonjour"), gateway=None, session_store=None) is None
    assert hermes_plugin._STATE.loop is None
