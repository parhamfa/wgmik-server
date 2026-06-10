"""Telegram bot lifecycle: start / stop / status.

The bot runs aiogram long-polling in a background thread so it doesn't block
the FastAPI event loop or APScheduler.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from ..db import SessionLocal
from ..models import SettingsKV
from ..security import SecretBox
from ..settings import settings
from .i18n import t

logger = logging.getLogger("wgmik.telegram")

_bot_thread: Optional[threading.Thread] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_stop_event: Optional[asyncio.Event] = None
_bot_started_at: Optional[datetime] = None
_bot_running: bool = False


def _bot_commands_for_language(lang: str):
    from aiogram.types import BotCommand

    return [
        BotCommand(command="start", description=t("cmd_desc_start", lang)),
        BotCommand(command="home", description=t("cmd_desc_home", lang)),
        BotCommand(command="today", description=t("cmd_desc_today", lang)),
        BotCommand(command="monthly", description=t("cmd_desc_monthly", lang)),
        BotCommand(command="alltime", description=t("cmd_desc_alltime", lang)),
        BotCommand(command="calendar", description=t("cmd_desc_calendar", lang)),
        BotCommand(command="fair", description=t("cmd_desc_fair", lang)),
        BotCommand(command="settings", description=t("cmd_desc_settings", lang)),
    ]


async def _sync_bot_commands(bot) -> None:
    await bot.set_my_commands(_bot_commands_for_language("en"))
    await bot.set_my_commands(_bot_commands_for_language("fa"), language_code="fa")


def _get_tg_settings() -> dict:
    db = SessionLocal()
    try:
        result = {}
        for key in ("tg_bot_token", "tg_bot_enabled", "tg_admin_chat_id", "tg_bot_language"):
            kv = db.get(SettingsKV, key)
            result[key] = kv.value if kv else ""
        return result
    finally:
        db.close()


def _decrypt_token(encrypted: str) -> str:
    if not encrypted:
        return ""
    box = SecretBox(settings.secret_key)
    plain = box.decrypt(encrypted)
    return plain or encrypted  # fallback: maybe stored plaintext during dev


def get_bot_status() -> dict:
    return {
        "running": _bot_running,
        "started_at": _bot_started_at.isoformat() if _bot_started_at else None,
        "uptime_seconds": int((datetime.now(timezone.utc) - _bot_started_at).total_seconds()) if _bot_started_at and _bot_running else 0,
    }


def _run_bot(token: str) -> None:
    global _bot_loop, _bot_stop_event, _bot_running, _bot_started_at

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bot_loop = loop
    _bot_stop_event = asyncio.Event()

    async def _main() -> None:
        global _bot_running, _bot_started_at
        from aiogram import Bot, Dispatcher
        from .handlers import register_handlers

        bot = Bot(token=token)
        try:
            await _sync_bot_commands(bot)
        except Exception:
            logger.exception("Telegram bot command sync failed")
        dp = Dispatcher()
        register_handlers(dp)

        _bot_running = True
        _bot_started_at = datetime.now(timezone.utc)
        logger.info("Telegram bot started (long-polling)")

        try:
            polling_task = asyncio.create_task(
                dp.start_polling(bot, handle_signals=False)
            )
            stop_task = asyncio.create_task(_bot_stop_event.wait())
            done, pending = await asyncio.wait(
                {polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await dp.stop_polling()
            except RuntimeError:
                pass
            await bot.session.close()
        finally:
            _bot_running = False
            logger.info("Telegram bot stopped")

    try:
        loop.run_until_complete(_main())
    except Exception:
        logger.exception("Telegram bot crashed")
        _bot_running = False
    finally:
        loop.close()
        _bot_loop = None


def start_bot() -> bool:
    """Start the bot if a valid token is configured and enabled. Returns True if started."""
    global _bot_thread
    if _bot_running:
        return True

    cfg = _get_tg_settings()
    if cfg.get("tg_bot_enabled", "").lower() not in ("true", "1", "yes"):
        logger.info("Telegram bot is disabled in settings")
        return False

    token = _decrypt_token(cfg.get("tg_bot_token", ""))
    if not token or len(token) < 20:
        logger.warning("No valid Telegram bot token configured")
        return False

    _bot_thread = threading.Thread(target=_run_bot, args=(token,), daemon=True, name="tg-bot")
    _bot_thread.start()
    return True


def stop_bot() -> None:
    global _bot_thread
    if _bot_stop_event and _bot_loop:
        _bot_loop.call_soon_threadsafe(_bot_stop_event.set)
    if _bot_thread:
        _bot_thread.join(timeout=10)
        _bot_thread = None


def restart_bot() -> bool:
    stop_bot()
    return start_bot()


def get_bot_instance():
    """Return the running Bot instance (for sending notifications), or None."""
    if not _bot_running or not _bot_loop:
        return None
    # We need to import lazily to avoid circular imports
    from aiogram import Bot
    cfg = _get_tg_settings()
    token = _decrypt_token(cfg.get("tg_bot_token", ""))
    if not token:
        return None
    return Bot(token=token), _bot_loop
