"""Telegram bot command and callback handlers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery

from ..db import SessionLocal
from ..models import (
    Peer,
    Router as RouterModel,
    TelegramPeerBinding,
    TelegramUser,
)
from .formatters import format_fair_usage_status, format_peer_line, format_usage_scope
from .i18n import t
from .keyboards import (
    back_button,
    main_menu,
    peer_list_keyboard,
    settings_menu,
    usage_scope_selector,
)
from .tokens import redeem_token

logger = logging.getLogger("wgmik.telegram.handlers")
router = Router()


def _get_tg_user(tg_id: int) -> TelegramUser | None:
    db = SessionLocal()
    try:
        return db.query(TelegramUser).filter_by(telegram_user_id=tg_id).first()
    finally:
        db.close()


def _get_lang(tg_id: int) -> str:
    user = _get_tg_user(tg_id)
    return user.language if user else "en"


def _get_visible_peers(tg_user_id: int) -> list[tuple[Peer, RouterModel | None]]:
    db = SessionLocal()
    try:
        tg_user = db.query(TelegramUser).filter_by(telegram_user_id=tg_user_id).first()
        if not tg_user:
            return []
        bindings = (
            db.query(TelegramPeerBinding)
            .filter_by(telegram_user_id=tg_user.id, visible=True)
            .all()
        )
        result = []
        for b in bindings:
            peer = db.get(Peer, b.peer_id)
            if peer:
                rtr = db.get(RouterModel, peer.router_id)
                result.append((peer, rtr))
        return result
    finally:
        db.close()


@router.message(CommandStart(deep_link=True))
async def cmd_start_deeplink(msg: types.Message):
    """Handle /start with a signup token deep link."""
    token_str = msg.text.split(maxsplit=1)[1] if len(msg.text.split()) > 1 else ""
    if not token_str:
        return await cmd_start(msg)

    tg_id = msg.from_user.id

    # Check if already blocked
    existing = _get_tg_user(tg_id)
    if existing and existing.is_blocked:
        lang = existing.language or "en"
        return await msg.answer(t("blocked", lang))

    db = SessionLocal()
    try:
        tg_user, error = redeem_token(
            db,
            token_str,
            tg_id,
            tg_username=msg.from_user.username or "",
            first_name=msg.from_user.first_name or "",
            last_name=msg.from_user.last_name or "",
        )
        if error:
            lang = _get_lang(tg_id)
            await msg.answer(t(error, lang))
            db.rollback()
            return

        db.commit()
        bindings = db.query(TelegramPeerBinding).filter_by(telegram_user_id=tg_user.id).count()
        lang = tg_user.language or "en"
        await msg.answer(
            t("welcome_signup", lang, count=str(bindings)),
            reply_markup=main_menu(lang),
        )
    except Exception:
        db.rollback()
        logger.exception("Token redemption failed")
        await msg.answer(t("token_invalid", "en"))
    finally:
        db.close()


@router.message(CommandStart())
async def cmd_start(msg: types.Message):
    """Handle plain /start (no deep link)."""
    tg_id = msg.from_user.id
    user = _get_tg_user(tg_id)
    if user and user.is_blocked:
        return await msg.answer(t("blocked", user.language))

    lang = user.language if user else "en"
    if not user:
        await msg.answer(t("not_registered", lang))
        return

    await msg.answer(t("welcome", lang), reply_markup=main_menu(lang))


@router.callback_query(lambda c: c.data == "menu:main")
async def cb_main_menu(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    await cb.message.edit_text(t("welcome", lang), reply_markup=main_menu(lang))
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:peers")
async def cb_peers(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    peers = _get_visible_peers(cb.from_user.id)
    if not peers:
        await cb.message.edit_text(t("no_peers", lang), reply_markup=back_button(lang))
        await cb.answer()
        return

    lines = []
    peer_buttons = []
    for peer, rtr in peers:
        lines.append(format_peer_line(peer, rtr, lang))
        peer_buttons.append((peer.id, peer.name or peer.public_key[:12]))

    text = "\n".join(lines)
    await cb.message.edit_text(text, reply_markup=peer_list_keyboard(peer_buttons, lang), parse_mode="Markdown")
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:usage")
async def cb_usage_menu(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    await cb.message.edit_text(
        t("usage_header", lang, scope="..."),
        reply_markup=usage_scope_selector(lang),
    )
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("usage:"))
async def cb_usage_scope(cb: CallbackQuery):
    scope = cb.data.split(":")[1]
    lang = _get_lang(cb.from_user.id)
    peers = _get_visible_peers(cb.from_user.id)
    if not peers:
        await cb.message.edit_text(t("no_peers", lang), reply_markup=back_button(lang))
        await cb.answer()
        return

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        lines = []
        for peer, _ in peers:
            db_peer = db.get(Peer, peer.id)
            if db_peer:
                lines.append(format_usage_scope(db_peer, scope, db, lang, now))
        text = "\n\n".join(lines) if lines else t("no_peers", lang)
        await cb.message.edit_text(text, reply_markup=usage_scope_selector(lang), parse_mode="Markdown")
    finally:
        db.close()
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:fair_usage")
async def cb_fair_usage(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    peers = _get_visible_peers(cb.from_user.id)
    if not peers:
        await cb.message.edit_text(t("no_peers", lang), reply_markup=back_button(lang))
        await cb.answer()
        return

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        lines = []
        for peer, _ in peers:
            db_peer = db.get(Peer, peer.id)
            if db_peer:
                lines.append(format_fair_usage_status(db_peer, db, lang, now))
        text = "\n\n".join(lines) if lines else t("no_peers", lang)
        await cb.message.edit_text(text, reply_markup=back_button(lang), parse_mode="Markdown")
    finally:
        db.close()
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:settings")
async def cb_settings(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    await cb.message.edit_text(t("btn_settings", lang), reply_markup=settings_menu(lang))
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("settings:lang:"))
async def cb_switch_lang(cb: CallbackQuery):
    new_lang = cb.data.split(":")[-1]
    if new_lang not in ("en", "fa"):
        new_lang = "en"

    db = SessionLocal()
    try:
        tg_user = db.query(TelegramUser).filter_by(telegram_user_id=cb.from_user.id).first()
        if tg_user:
            tg_user.language = new_lang
            db.commit()
    finally:
        db.close()

    await cb.message.edit_text(
        t("lang_switched", new_lang),
        reply_markup=main_menu(new_lang),
    )
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("peer:"))
async def cb_peer_detail(cb: CallbackQuery):
    peer_id = int(cb.data.split(":")[1])
    lang = _get_lang(cb.from_user.id)

    db = SessionLocal()
    try:
        peer = db.get(Peer, peer_id)
        if not peer:
            await cb.message.edit_text(t("no_peers", lang), reply_markup=back_button(lang))
            await cb.answer()
            return

        rtr = db.get(RouterModel, peer.router_id)
        now = datetime.now(timezone.utc)

        peer_info = format_peer_line(peer, rtr, lang)
        usage_text = format_usage_scope(peer, "month", db, lang, now)
        fu_text = format_fair_usage_status(peer, db, lang, now)

        text = f"{peer_info}\n\n{usage_text}\n\n{fu_text}"
        await cb.message.edit_text(text, reply_markup=back_button(lang), parse_mode="Markdown")
    finally:
        db.close()
    await cb.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
