from unittest.mock import AsyncMock

from assistant.bot import send_replies
from assistant.controller import Reply
from assistant.models import Action
from assistant.render import Button


async def test_send_sets_message_id_and_buttons(store):
    bot = AsyncMock()
    bot.send_message.return_value.message_id = 55
    p = store.create([Action(kind="create", content="x")], "x")
    await send_replies(bot, 1, [Reply("t", [Button("✅ Valider", f"ok:{p.id}")], proposal_id=p.id)], store)
    kw = bot.send_message.call_args.kwargs
    assert kw["parse_mode"] == "HTML"
    assert kw["reply_markup"].inline_keyboard[0][0].callback_data == f"ok:{p.id}"
    assert store.get(p.id).message_id == 55


async def test_edit_reply_uses_edit(store):
    bot = AsyncMock()
    await send_replies(bot, 1, [Reply("✅ Fait", edit_message_id=10)], store)
    kw = bot.edit_message_text.call_args.kwargs
    assert kw["message_id"] == 10 and kw["parse_mode"] == "HTML"
    bot.send_message.assert_not_called()


async def test_long_text_split_buttons_on_last(store):
    bot = AsyncMock()
    await send_replies(bot, 1, [Reply("x\n" * 5000, [Button("b", "ok:1")])], store)
    calls = bot.send_message.call_args_list
    assert len(calls) >= 3
    assert calls[0].kwargs["reply_markup"] is None and calls[-1].kwargs["reply_markup"] is not None
