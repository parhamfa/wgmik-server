"""Shared synchronous wrappers around Telegram Bot API calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class TelegramSenderUnavailable(RuntimeError):
    """Raised when a send cannot even be attempted because bot settings are missing."""


def run_bot_coro(make_coro: Callable[[object], Awaitable[T]]) -> T:
    """Run one Bot API coroutine on the polling loop when possible, otherwise in a short-lived loop."""
    from .bot import _decrypt_token, _get_tg_settings

    cfg = _get_tg_settings()
    token = _decrypt_token(cfg.get("tg_bot_token", ""))
    if not token or len(token) < 20:
        raise TelegramSenderUnavailable("No valid Telegram bot token configured")

    async def _do_send() -> T:
        from aiogram import Bot

        bot = Bot(token=token)
        try:
            return await make_coro(bot)
        finally:
            await bot.session.close()

    from .bot import _bot_loop, _bot_running

    if _bot_running and _bot_loop and not _bot_loop.is_closed():
        future = asyncio.run_coroutine_threadsafe(_do_send(), _bot_loop)
        return future.result(timeout=30)
    return asyncio.run(_do_send())


def send_message_sync(
    chat_id: int,
    text: str,
    *,
    reply_markup=None,
):
    return run_bot_coro(
        lambda bot: bot.send_message(chat_id, text, reply_markup=reply_markup)
    )


def send_photo_sync(
    chat_id: int,
    photo_bytes: bytes,
    caption: str,
    *,
    filename: str = "photo.jpg",
    reply_markup=None,
):
    from aiogram.types import BufferedInputFile

    return run_bot_coro(
        lambda bot: bot.send_photo(
            chat_id,
            BufferedInputFile(photo_bytes, filename=filename),
            caption=caption[:1024],
            reply_markup=reply_markup,
        )
    )
