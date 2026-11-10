"""Plugin Hermes « todoist-assistant » : colle entre l'API de plugins Hermes (v0.23.1) et le cœur.

Le paquet `assistant/` est copié à côté de ce fichier par `deploy/install_plugin.sh`.
Les imports Telegram et Hermes restent dans les fonctions : le module se charge sans eux.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from assistant.config import PluginConfig, load_config  # noqa: E402
from assistant.db import connect  # noqa: E402
from assistant.executor import Executor  # noqa: E402
from assistant.hermes.ports import Button  # noqa: E402
from assistant.hermes.prompt import PROMPT_SECTION  # noqa: E402
from assistant.hermes.relay import Relay  # noqa: E402
from assistant.hermes.todoist_tools import TOOL_SCHEMAS, ProposalFlow, run_read_tool  # noqa: E402
from assistant.hermes.voice import TranscriptionError, VoiceFlow  # noqa: E402
from assistant.pending_store import PendingStore  # noqa: E402
from assistant.todoist_client import TodoistClient  # noqa: E402

log = logging.getLogger("todoist-assistant")

PLUGIN_NAME = "todoist-assistant"
TOOLSET = "todoist"
TICK_SECONDS = 15
NOT_CONNECTED = "Telegram n'est pas connecté : impossible d'envoyer la carte de validation."
READ_TOOLS = ("todoist_list_projects", "todoist_search_tasks", "todoist_get_task")
MAX_REMEMBERED_VOICES = 256
SKIP_VOICE = {"action": "skip", "reason": "todoist-assistant: vocal non validé"}

_STATE: "_Plugin | None" = None


def parse_callback(data: str) -> tuple[str, str, str] | None:
    """`"ta:v:send:k"` → `("v", "send", "k")` ; `None` hors préfixe `ta:` ou mal formé."""
    if not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "ta" or parts[1] not in ("v", "p"):
        return None
    if not parts[2] or not parts[3]:
        return None
    return parts[1], parts[2], parts[3]


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _platform_name(platform) -> str:
    return str(getattr(platform, "value", platform) or "").lower()


def _transcribe(audio: bytes) -> str:
    """Seul point de contact avec l'API interne de transcription de Hermes."""
    path = None
    try:
        from tools.transcription_tools import transcribe_audio
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio)
            path = f.name
        result = transcribe_audio(path, None, "gateway")
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else None
            raise TranscriptionError(error or "transcription impossible")
        return str(result.get("transcript") or "")
    except TranscriptionError:
        raise
    except Exception as e:  # toute erreur devient une TranscriptionError
        raise TranscriptionError(str(e) or type(e).__name__) from e
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass


class _TelegramMessenger:
    def __init__(self, plugin: "_Plugin"):
        self._plugin = plugin

    def _bot(self):
        if self._plugin.bot is None:
            raise RuntimeError(NOT_CONNECTED)
        return self._plugin.bot

    @staticmethod
    def _markup(buttons: list[Button] | None):
        if not buttons:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(b.text, callback_data=b.data) for b in buttons]])

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> int:
        msg = await self._bot().send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                                             reply_markup=self._markup(buttons))
        return msg.message_id

    async def edit(self, chat_id: str, message_id: int, text: str,
                   buttons: list[Button] | None = None) -> None:
        await self._bot().edit_message_text(text=text, chat_id=chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=self._markup(buttons))


# --- vocal validé → message texte synthétique --------------------------------------------------
#
# Le texte validé est remis au cœur Telegram de Hermes comme un vrai message texte de l'utilisateur
# (`application.process_update`) : la gateway l'admet comme n'importe quel tour humain, y compris en
# session neuve (après /new) ou après un redémarrage, là où `ctx.inject_message` était refusé.
#
# Choix des identifiants (lus dans plugins/platforms/telegram/update_admission.py de Hermes) :
# - update_id : `TelegramApplication.process_update` ignore toute mise à jour dont la clé
#   `<bot_id>:<update_id>` est déjà dans `_seen_update_ids` (reçus persistés 24 h sur disque) ou
#   `_inflight_update_ids`. L'update du vocal y est déjà : notre handler l'a acceptée
#   (`claim.accepted`) avant d'arrêter la propagation. On prend donc un update_id NÉGATIF, unique et
#   décroissant (horloge en microsecondes) : Telegram n'attribue que des update_id positifs, aucune
#   collision possible avec un vrai update, ni avec un update synthétique d'avant un redémarrage
#   (les reçus acceptent les ids négatifs). L'offset de polling (`Updater._last_update_id`) n'est
#   mis à jour que par la boucle getUpdates de PTB, jamais par `process_update` : il n'est pas touché.
# - message_id : celui du vocal d'origine (jamais admis par la gateway, puisque le plugin l'a
#   arrêté). Hermes s'en sert comme identité d'admission (`request_id`) et comme ancre de réponse :
#   la réponse cite le vrai vocal, et un « Renvoyer » après une première remise réellement admise
#   est reconnu comme rejeu au lieu de créer un second tour.
# Le texte n'a pas d'entités : même s'il commence par « / », ce n'est pas une commande Telegram.

_last_synthetic_id = 0
_synthetic_lock = threading.Lock()


def _next_synthetic_update_id() -> int:
    global _last_synthetic_id
    with _synthetic_lock:
        candidate = -(time.time_ns() // 1000)
        _last_synthetic_id = min(candidate, _last_synthetic_id - 1)
        return _last_synthetic_id


_MEDIA_FIELDS = ("voice", "audio", "caption", "caption_entities", "entities", "media_group_id",
                 "show_caption_above_media", "has_media_spoiler")


def build_text_update(message, text: str, update_id: int, bot):
    """Update Telegram : `message` (le vocal) devenu un simple message texte `text`."""
    from telegram import Message, Update
    data = message.to_dict()
    for field in _MEDIA_FIELDS:
        data.pop(field, None)
    data["text"] = text
    return Update(update_id=update_id, message=Message.de_json(data, bot))


class _TelegramSubmitter:
    def __init__(self, plugin: "_Plugin"):
        self._plugin = plugin
        self._lock = threading.Lock()
        self._messages: "OrderedDict[str, object]" = OrderedDict()

    def remember(self, message) -> str:
        source = f"{message.chat_id}:{message.message_id}"
        with self._lock:
            self._messages[source] = message
            while len(self._messages) > MAX_REMEMBERED_VOICES:
                self._messages.popitem(last=False)
        return source

    async def submit(self, chat_id: str, source: str, text: str) -> None:
        with self._lock:
            message = self._messages.get(source)
        app = self._plugin.application
        if message is None or str(message.chat_id) != chat_id:
            raise RuntimeError(f"vocal d'origine introuvable : {source}")
        if app is None:
            raise RuntimeError(NOT_CONNECTED)
        update = build_text_update(message, text, _next_synthetic_update_id(), app.bot)
        self._plugin.mark_synthetic(chat_id, str(message.message_id))
        await app.process_update(update)


async def _guard(coro, what: str) -> None:
    try:
        await coro
    except Exception:
        log.exception("%s a échoué", what)


class _Plugin:
    def __init__(self, ctx, cfg: PluginConfig):
        self.ctx = ctx
        self.cfg = cfg
        self.bot = None
        self.application = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tick_task: asyncio.Task | None = None
        clock = lambda: datetime.now(cfg.timezone)  # noqa: E731
        self.todoist = TodoistClient(cfg.todoist_token)
        self.store = PendingStore(connect(cfg.db_path), clock)
        self.executor = Executor(self.todoist, self.store)
        self.messenger = _TelegramMessenger(self)
        self.relay = Relay(cfg.allowed_user_ids, clock)
        self.submitter = _TelegramSubmitter(self)
        self._synthetic: "OrderedDict[tuple[str, str], None]" = OrderedDict()
        self.voice = VoiceFlow(self.messenger, self.submitter, _transcribe, clock)
        self.proposals = ProposalFlow(self.store, self.executor, self.todoist, self.messenger,
                                      self.relay, cfg.owner_chat_id)

    def _allowed(self, user) -> bool:
        return user is not None and str(user.id) in self.cfg.allowed_user_ids

    def mark_synthetic(self, chat_id: str, message_id: str) -> None:
        self._synthetic[(chat_id, message_id)] = None
        while len(self._synthetic) > MAX_REMEMBERED_VOICES:
            self._synthetic.popitem(last=False)

    def is_synthetic(self, chat_id: str, message_id: str) -> bool:
        return (chat_id, message_id) in self._synthetic

    # --- Telegram ---------------------------------------------------------

    def _capture_loop(self) -> None:
        if self.loop is not None:
            return
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tick_task = self.loop.create_task(self._tick_loop())
        log.info("todoist-assistant : boucle asyncio capturée")

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            await _guard(self.voice.tick(), "VoiceFlow.tick")

    def wire(self, application, adapter=None) -> None:
        from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, MessageHandler, filters

        self.application = application
        self.bot = application.bot
        self._capture_loop()

        async def voice_handler(update, context):
            self._capture_loop()
            if await self.on_voice(update, context):
                raise ApplicationHandlerStop  # le vocal ne doit pas partir à Hermes sans validation

        async def callback_handler(update, context):
            self._capture_loop()
            await self.on_callback(update, context)
            raise ApplicationHandlerStop  # préfixe ta: propre au plugin

        application.add_handler(MessageHandler(
            filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO), voice_handler))
        application.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^ta:"))
        log.info("todoist-assistant branché sur Telegram")

    async def on_voice(self, update, context) -> bool:
        """Renvoie True si le vocal a été pris en charge par le plugin."""
        if not self._allowed(getattr(update, "effective_user", None)):
            return False
        msg = update.effective_message
        media = msg.voice or msg.audio
        if media is None:
            return False
        try:
            file = await media.get_file()
            audio = bytes(await file.download_as_bytearray())
            source = self.submitter.remember(msg)
            await self.voice.on_voice(str(msg.chat_id), audio, source)
        except Exception:
            log.exception("traitement du vocal en échec")
        return True

    async def on_callback(self, update, context) -> None:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            log.exception("query.answer a échoué")
        if not self._allowed(getattr(query, "from_user", None)):
            return
        parsed = parse_callback(query.data)
        message = getattr(query, "message", None)
        if parsed is None or message is None:
            return
        # Les cartes ne vivent que dans le message privé du propriétaire.
        if getattr(getattr(message, "chat", None), "type", None) != "private":
            return
        kind, verb, key = parsed
        chat_id, message_id = str(message.chat_id), message.message_id
        flow = self.voice if kind == "v" else self.proposals
        await _guard(flow.on_button(chat_id, message_id, verb, key), f"clic {query.data}")

    # --- hooks ------------------------------------------------------------

    def pre_gateway_dispatch(self, event=None, gateway=None, session_store=None, **kwargs):
        try:
            source = event.source
            if _platform_name(source.platform) != "telegram":
                return None
            # Après un redémarrage, `wire` peut être appelé avant que la boucle tourne. Ce hook est
            # exécuté en ligne sur la boucle de la gateway (`asyncio.run` du thread principal) :
            # on la capture ici, jamais depuis un thread auxiliaire qui aurait sa propre boucle.
            if self.loop is None and threading.current_thread() is threading.main_thread():
                self._capture_loop()
            user_id = None if source.user_id is None else str(source.user_id)
            allowed = user_id is not None and user_id in self.cfg.allowed_user_ids
            chat_id = str(source.chat_id)
            kind = _platform_name(getattr(event, "message_type", None))
            # Second garde-fou : un vocal du propriétaire en privé n'atteint jamais Hermes sans
            # validation, même si une mise à jour de Hermes changeait l'ordre des handlers.
            if allowed and kind in ("voice", "audio") and getattr(source, "chat_type", None) == "dm":
                return SKIP_VOICE
            synthetic = self.is_synthetic(chat_id, str(getattr(event, "message_id", None)))
            text = self.relay.rewrite(user_id, chat_id, event.text or "", synthetic=synthetic)
            return {"action": "rewrite", "text": text} if text is not None else None
        except Exception:
            log.exception("pre_gateway_dispatch a échoué")
            return None

    def pre_llm_call(self, session_id=None, user_message=None, conversation_history=None,
                     is_first_turn=None, model=None, platform=None, **kwargs):
        try:
            if self.loop is not None and isinstance(user_message, str):
                asyncio.run_coroutine_threadsafe(
                    _guard(self.voice.on_llm_call(user_message), "VoiceFlow.on_llm_call"),
                    self.loop)
        except Exception:
            log.exception("pre_llm_call a échoué")
        return None

    # --- outils -----------------------------------------------------------

    def read_tool(self, name: str):
        def handler(args: dict | None = None, **kwargs) -> str:
            try:
                text = run_read_tool(name, args or {}, self.todoist)
                return json.dumps({"result": text}, ensure_ascii=False)
            except Exception as e:
                log.exception("outil %s en échec", name)
                return _err(str(e) or type(e).__name__)
        return handler

    async def propose_tool(self, args: dict | None = None, **kwargs) -> str:
        try:
            if self.bot is None or self.loop is None:
                return _err(NOT_CONNECTED)
            fut = asyncio.run_coroutine_threadsafe(self.proposals.propose(args or {}), self.loop)
            return await asyncio.wrap_future(fut)
        except Exception as e:
            log.exception("todoist_propose_actions en échec")
            return _err(str(e) or type(e).__name__)


def _data_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "plugin-data" / PLUGIN_NAME


def _token_file() -> Path:
    """Jeton Todoist : hors de HERMES_HOME, dans le home réel de l'utilisateur qui fait tourner Hermes."""
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / PLUGIN_NAME / "token"


def register(ctx) -> None:
    global _STATE
    # Un ancien jeton laissé dans l'environnement pourrait être transmis au conteneur de l'agent.
    os.environ.pop("TODOIST_API_TOKEN", None)
    cfg = load_config(os.environ, _data_dir(), _token_file())
    plugin = _Plugin(ctx, cfg)
    _STATE = plugin

    ctx.register_platform_handler("telegram", plugin.wire)

    ctx.register_hook("pre_gateway_dispatch", plugin.pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", plugin.pre_llm_call)

    for name in READ_TOOLS:
        schema = TOOL_SCHEMAS[name]
        ctx.register_tool(name, TOOLSET, schema, plugin.read_tool(name),
                          description=schema["description"])
    schema = TOOL_SCHEMAS["todoist_propose_actions"]
    ctx.register_tool("todoist_propose_actions", TOOLSET, schema, plugin.propose_tool,
                      is_async=True, description=schema["description"])

    ctx.register_system_prompt_section(f"{PLUGIN_NAME}.rules", PROMPT_SECTION)
