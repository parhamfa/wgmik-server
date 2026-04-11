"""Telegram bot command and callback handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..db import SessionLocal
from ..fair_usage_peer_status_dto import build_fair_usage_peer_status_dto
from ..models import Peer, Router as RouterModel, TelegramPeerBinding, TelegramUser
from ..settings import settings
from .fair_usage_image import render_fair_usage_peer_card_png
from .formatters import format_next_reset_from_iso
from .usage_chart_image import render_usage_chart_png, usage_points_for_tg_menu
from .i18n import t
from .keyboards import (
    home_button,
    language_menu,
    main_menu,
    notifications_menu,
    settings_menu,
    usage_history_menu,
)
from .notifications import (
    USER_NOTIFICATION_EVENT_TYPES,
    get_user_notification_preferences,
    set_user_notification_preference,
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


async def _abort_if_not_registered_or_blocked(msg: Message) -> bool:
    """Return True if the handler should stop (user missing or blocked)."""
    tg_id = msg.from_user.id
    user = _get_tg_user(tg_id)
    lang = user.language if user else "en"
    if not user:
        await msg.answer(t("not_registered", lang))
        return True
    if user.is_blocked:
        await msg.answer(t("blocked", user.language or "en"))
        return True
    return False

def _notification_label(lang: str, event_type: str, enabled: bool) -> str:
    marker = "✅" if enabled else "☑️"
    return f"{marker} {t(f'notif_label_{event_type}', lang)}"


async def _safe_edit_text(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def _status_caption_text(dto, peer_label: str, lang: str) -> str:
    status_label = t("fu_throttled", lang) if dto.throttled else t("fu_ok", lang)
    lines = [
        t("status_card_caption", lang, name=peer_label),
        f"{t('btn_status', lang)}: {status_label}",
    ]
    if dto.throttled:
        effective_rule = next((rule for rule in dto.rules if getattr(rule, "is_effective", False)), None) or (dto.rules[0] if dto.rules else None)
        rule_label = effective_rule.rule_name if effective_rule else t("status_no_active_rule", lang)
        reset_line = (
            format_next_reset_from_iso(effective_rule.next_reset, lang)
            if effective_rule and effective_rule.next_reset
            else t("status_next_reset", lang) + ": " + t("status_none", lang)
        )
        lines.extend([
            f"{t('status_effective_rule', lang)}: {rule_label}",
            reset_line,
        ])
    return "\n".join(lines)


async def deliver_usage_scope_charts(
    message: Message,
    scope: str,
    *,
    use_edit: bool,
    requester_id: int,
) -> None:
    """Send usage charts for all visible peers."""
    lang = _get_lang(requester_id)
    peers = _get_visible_peers(requester_id)
    if not peers:
        if use_edit:
            await _safe_edit_text(message, t("no_peers", lang), reply_markup=home_button(lang))
        else:
            await message.answer(t("no_peers", lang))
        return

    scope_label = {
        "today": t("btn_today", lang),
        "week": t("btn_this_week", lang),
        "month": t("btn_this_month", lang),
    }.get(scope, scope)

    status_text = t("usage_sending", lang, scope=scope_label)
    if use_edit:
        await _safe_edit_text(message, status_text, reply_markup=usage_history_menu(lang))
    else:
        await message.answer(status_text)

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        for peer, _ in peers:
            db_peer = db.get(Peer, peer.id)
            if not db_peer:
                continue
            points, mode = usage_points_for_tg_menu(db, db_peer.id, scope, now)
            label = db_peer.name or db_peer.public_key[:12]
            payload = {
                "peerName": label,
                "scopeLabel": scope_label,
                "mode": mode,
                "timezone": settings.timezone,
                "points": points,
            }
            try:
                png = await render_usage_chart_png(payload)
            except Exception:
                logger.exception("Usage chart screenshot failed for peer %s", db_peer.id)
                continue
            await message.answer_photo(
                BufferedInputFile(png, filename="usage.png"),
                caption=t("usage_chart_caption", lang, name=label, scope=scope_label),
            )
    finally:
        db.close()


async def deliver_status_cards(message: Message, *, use_edit: bool, requester_id: int) -> None:
    """Send one status card per visible peer, then explain tiers/limits."""
    lang = _get_lang(requester_id)
    peers = _get_visible_peers(requester_id)
    if not peers:
        if use_edit:
            await _safe_edit_text(message, t("no_peers", lang), reply_markup=home_button(lang))
        else:
            await message.answer(t("no_peers", lang))
        return

    status_text = t("status_sending", lang)
    if use_edit:
        await _safe_edit_text(message, status_text, reply_markup=home_button(lang))
    else:
        await message.answer(status_text)

    sent = 0
    db = SessionLocal()
    try:
        for peer, _ in peers:
            db_peer = db.get(Peer, peer.id)
            if not db_peer:
                continue
            dto = build_fair_usage_peer_status_dto(db, db_peer)
            label = db_peer.name or db_peer.public_key[:12]
            try:
                png = await render_fair_usage_peer_card_png(dto, label, lang)
            except Exception:
                logger.exception("Fair usage screenshot failed for peer %s", db_peer.id)
                continue
            await message.answer_photo(
                BufferedInputFile(png, filename="fair-usage.png"),
                caption=_status_caption_text(dto, label, lang),
            )
            sent += 1
    finally:
        db.close()

    if sent == 0:
        await message.answer(t("status_failed", lang))


async def deliver_settings_screen(message: Message, *, use_edit: bool, requester_id: int) -> None:
    lang = _get_lang(requester_id)
    text = t("settings_intro", lang)
    if use_edit:
        await _safe_edit_text(message, text, reply_markup=settings_menu(lang))
    else:
        await message.answer(text, reply_markup=settings_menu(lang))


async def deliver_language_screen(message: Message, *, use_edit: bool, requester_id: int) -> None:
    lang = _get_lang(requester_id)
    text = t("language_title", lang)
    if use_edit:
        await _safe_edit_text(message, text, reply_markup=language_menu(lang))
    else:
        await message.answer(t("settings_command_hint", lang))


async def deliver_notifications_screen(message: Message, *, use_edit: bool, requester_id: int) -> None:
    lang = _get_lang(requester_id)
    user = _get_tg_user(requester_id)
    prefs: dict[str, bool] = {}
    if user:
        db = SessionLocal()
        try:
            prefs = get_user_notification_preferences(db, user.id)
        finally:
            db.close()
    items = [
        (event_type, _notification_label(lang, event_type, prefs.get(event_type, True)))
        for event_type in USER_NOTIFICATION_EVENT_TYPES
    ]
    text = t("notifications_intro", lang)
    if use_edit:
        await _safe_edit_text(message, text, reply_markup=notifications_menu(items, lang))
    else:
        await message.answer(t("settings_command_hint", lang))


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
        await msg.answer(t("token_invalid", _get_lang(tg_id)))
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


@router.message(Command("home"))
async def cmd_home(msg: types.Message):
    """Same as /start: show welcome and main menu."""
    await cmd_start(msg)


@router.callback_query(lambda c: c.data == "menu:main")
async def cb_main_menu(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    await _safe_edit_text(cb.message, t("welcome", lang), reply_markup=main_menu(lang))
    await cb.answer()

@router.callback_query(lambda c: c.data == "menu:usage_history" or c.data == "menu:usage")
async def cb_usage_menu(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    peers = _get_visible_peers(cb.from_user.id)
    if not peers:
        await _safe_edit_text(cb.message, t("no_peers", lang), reply_markup=home_button(lang))
        await cb.answer()
        return
    await _safe_edit_text(
        cb.message,
        t("usage_history_intro", lang),
        reply_markup=usage_history_menu(lang),
    )
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("usage:"))
async def cb_usage_scope_legacy(cb: CallbackQuery):
    scope = cb.data.split(":")[1]
    await deliver_usage_scope_charts(cb.message, scope, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:status" or c.data == "menu:fair_usage")
async def cb_status(cb: CallbackQuery):
    await deliver_status_cards(cb.message, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer()


@router.callback_query(lambda c: c.data == "menu:settings")
async def cb_settings(cb: CallbackQuery):
    await deliver_settings_screen(cb.message, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer()


@router.callback_query(lambda c: c.data == "settings:language" or c.data == "menu:language")
async def cb_language(cb: CallbackQuery):
    await deliver_language_screen(cb.message, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer()


@router.callback_query(lambda c: c.data == "settings:notifications")
async def cb_notifications(cb: CallbackQuery):
    await deliver_notifications_screen(cb.message, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer()


@router.message(Command("fair"))
async def cmd_fair(msg: types.Message):
    if await _abort_if_not_registered_or_blocked(msg):
        return
    await deliver_status_cards(msg, use_edit=False, requester_id=msg.from_user.id)


@router.message(Command("today"))
async def cmd_today(msg: types.Message):
    if await _abort_if_not_registered_or_blocked(msg):
        return
    await deliver_usage_scope_charts(msg, "today", use_edit=False, requester_id=msg.from_user.id)


@router.message(Command("weekly"))
async def cmd_weekly(msg: types.Message):
    if await _abort_if_not_registered_or_blocked(msg):
        return
    await deliver_usage_scope_charts(msg, "week", use_edit=False, requester_id=msg.from_user.id)


@router.message(Command("monthly"))
async def cmd_monthly(msg: types.Message):
    if await _abort_if_not_registered_or_blocked(msg):
        return
    await deliver_usage_scope_charts(msg, "month", use_edit=False, requester_id=msg.from_user.id)


@router.message(Command("settings"))
async def cmd_settings(msg: types.Message):
    if await _abort_if_not_registered_or_blocked(msg):
        return
    await deliver_settings_screen(msg, use_edit=False, requester_id=msg.from_user.id)


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

    await cb.message.edit_text(t("lang_switched", new_lang), reply_markup=settings_menu(new_lang))
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("settings:notif:"))
async def cb_toggle_notification(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    event_type = cb.data.split(":", 2)[-1]
    tg_user = _get_tg_user(cb.from_user.id)
    if not tg_user:
        await cb.answer(t("not_registered", lang), show_alert=True)
        return
    db = SessionLocal()
    try:
        prefs = get_user_notification_preferences(db, tg_user.id)
        current = prefs.get(event_type, True)
        if not set_user_notification_preference(db, tg_user.id, event_type, not current):
            await cb.answer()
            return
    finally:
        db.close()
    await deliver_notifications_screen(cb.message, use_edit=True, requester_id=cb.from_user.id)
    await cb.answer(t("settings_saved", lang))


@router.callback_query(lambda c: c.data and c.data.startswith("peer:"))
async def cb_peer_detail(cb: CallbackQuery):
    lang = _get_lang(cb.from_user.id)
    await _safe_edit_text(cb.message, t("welcome", lang), reply_markup=main_menu(lang))
    await cb.answer()


@router.message(F.text)
async def any_text_to_home(msg: types.Message):
    """Any text not handled above (plain text, unknown /commands): same as /start / /home."""
    await cmd_start(msg)


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
