"""Adaptateur python-telegram-bot : relie Telegram au Controller."""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from assistant.config import Config
from assistant.controller import Controller, Reply
from assistant.pending_store import PendingStore
from assistant.render import split_message

log = logging.getLogger(__name__)


def _markup(reply: Reply) -> InlineKeyboardMarkup | None:
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(b.text, callback_data=b.data)
                                  for b in reply.buttons]])


async def send_replies(bot, chat_id: int, replies: list[Reply], store: PendingStore) -> None:
    for reply in replies:
        if reply.edit_message_id is not None:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=reply.edit_message_id,
                                            text=split_message(reply.text)[0], parse_mode="HTML",
                                            reply_markup=_markup(reply),
                                            disable_web_page_preview=True)
                continue
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    continue
                log.warning("édition impossible (%s), envoi d'un nouveau message", e)
        parts = split_message(reply.text)
        sent = None
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            sent = await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML",
                                          reply_markup=_markup(reply) if last else None,
                                          disable_web_page_preview=True)
        if reply.proposal_id and sent is not None:
            store.set_message_id(reply.proposal_id, sent.message_id)


def build_application(config: Config, controller: Controller, store: PendingStore) -> Application:
    app = Application.builder().token(config.telegram_token).build()

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        replies = await asyncio.to_thread(controller.handle_text, update.effective_user.id, msg.text)
        await send_replies(context.bot, msg.chat_id, replies, store)

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        voice = msg.voice or msg.audio
        if update.effective_user.id != config.allowed_user_id:
            await asyncio.to_thread(controller.handle_text, update.effective_user.id, "")
            return
        if voice.duration and voice.duration > 300:
            audio = b""  # pas de téléchargement inutile : le contrôleur refuse avant transcription
        else:
            await context.bot.send_chat_action(msg.chat_id, "typing")
            f = await voice.get_file()
            audio = bytes(await f.download_as_bytearray())
        replies = await asyncio.to_thread(controller.handle_voice, update.effective_user.id,
                                          audio, voice.duration or 0)
        await send_replies(context.bot, msg.chat_id, replies, store)

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        replies = await asyncio.to_thread(controller.handle_callback, update.effective_user.id,
                                          query.data or "", query.message.message_id)
        await send_replies(context.bot, query.message.chat_id, replies, store)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("erreur non gérée", exc_info=context.error)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    return app
