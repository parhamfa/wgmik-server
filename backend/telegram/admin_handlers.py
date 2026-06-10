"""Admin-only Telegram bot handlers (gated by tg_admin_chat_id)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..calendar_utils import app_date_calendar
from ..db import SessionLocal
from ..models import Peer, SettingsKV, TelegramPeerBinding, TelegramUser
from ..settings import settings
from .formatters import format_usage_total_summary, usage_point_totals
from .i18n import t
from .keyboards import admin_menu, admin_user_list_keyboard, admin_user_report_menu
from .usage_chart_image import merge_usage_points, render_usage_chart_png, usage_points_for_tg_menu

logger = logging.getLogger("wgmik.telegram.admin_handlers")

router = Router()

_USERS_PER_PAGE = 8

_USAGE_SCOPE_LABEL_KEYS = {
    "today": "btn_today",
    "month": "btn_this_month",
    "alltime": "btn_all_time",
}


def _get_admin_chat_id() -> int | None:
    db = SessionLocal()
    try:
        kv = db.get(SettingsKV, "tg_admin_chat_id")
        if not kv or not str(kv.value).strip():
            return None
        return int(str(kv.value).strip())
    except (TypeError, ValueError):
        return None
    finally:
        db.close()


def _is_admin(tg_id: int) -> bool:
    admin_id = _get_admin_chat_id()
    return admin_id is not None and tg_id == admin_id


def _get_admin_lang() -> str:
    db = SessionLocal()
    try:
        kv = db.get(SettingsKV, "tg_bot_language")
        lang = (kv.value if kv else "") or "en"
        return lang if lang in ("en", "fa") else "en"
    finally:
        db.close()


async def _safe_edit_text(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def _scope_label(scope: str, lang: str) -> str:
    key = _USAGE_SCOPE_LABEL_KEYS.get(scope)
    return t(key, lang) if key else scope


def _user_label(user: TelegramUser) -> str:
    if user.telegram_username:
        return f"@{user.telegram_username}"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or f"User #{user.id}"


def _get_all_peers(db) -> list[Peer]:
    return db.query(Peer).order_by(Peer.name.asc(), Peer.id.asc()).all()


def _get_peers_for_tg_user(db, tg_user_db_id: int) -> list[Peer]:
    bindings = (
        db.query(TelegramPeerBinding)
        .filter_by(telegram_user_id=tg_user_db_id, visible=True)
        .all()
    )
    peers: list[Peer] = []
    for binding in bindings:
        peer = db.get(Peer, binding.peer_id)
        if peer:
            peers.append(peer)
    return peers


async def _deliver_peer_usage_charts(
    message: Message,
    peers: list[Peer],
    scope: str,
    *,
    lang: str,
    use_edit: bool,
    status_markup=None,
    done_markup=None,
) -> None:
    if not peers:
        text = t("adm_no_peers", lang)
        if use_edit:
            await _safe_edit_text(message, text, reply_markup=done_markup or admin_menu(lang))
        else:
            await message.answer(text, reply_markup=done_markup or admin_menu(lang))
        return

    scope_label = _scope_label(scope, lang)
    status_text = t("adm_usage_sending", lang, scope=scope_label)
    if use_edit:
        await _safe_edit_text(message, status_text, reply_markup=status_markup)
    else:
        await message.answer(status_text)

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        total_rx = 0
        total_tx = 0
        total_peers = 0
        for peer in peers:
            points, mode = usage_points_for_tg_menu(db, peer.id, scope, now)
            if not points and scope != "alltime":
                continue
            peer_rx, peer_tx = usage_point_totals(points)
            total_rx += peer_rx
            total_tx += peer_tx
            total_peers += 1
            label = peer.name or peer.public_key[:12]
            payload = {
                "peerName": label,
                "scopeLabel": scope_label,
                "mode": mode,
                "timezone": settings.timezone,
                "dateCalendar": app_date_calendar(),
                "points": points,
            }
            try:
                png = await render_usage_chart_png(payload)
            except Exception:
                logger.exception("Admin usage chart screenshot failed for peer %s", peer.id)
                continue
            await message.answer_photo(
                BufferedInputFile(png, filename="usage.png"),
                caption=t("usage_chart_caption", lang, name=label, scope=scope_label),
            )
        if total_peers > 1:
            await message.answer(
                format_usage_total_summary(scope_label, total_peers, total_rx, total_tx, lang)
            )
        if total_peers == 0:
            await message.answer(t("adm_no_usage_data", lang, scope=scope_label))
    finally:
        db.close()

    if done_markup:
        await message.answer(t("adm_done", lang), reply_markup=done_markup)


async def _deliver_dashboard_usage_chart(
    message: Message,
    scope: str,
    *,
    lang: str,
    use_edit: bool,
    done_markup=None,
) -> None:
    """One aggregate chart across all peers (the 'main dashboard' view), not per-peer spam."""
    scope_label = _scope_label(scope, lang)
    status_text = t("adm_usage_sending", lang, scope=scope_label)
    if use_edit:
        await _safe_edit_text(message, status_text)
    else:
        await message.answer(status_text)

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        peers = _get_all_peers(db)
        if not peers:
            await message.answer(t("adm_no_peers", lang), reply_markup=done_markup or admin_menu(lang))
            return

        series: list[list[dict]] = []
        mode = "days"
        total_rx = 0
        total_tx = 0
        active_peers = 0
        for peer in peers:
            points, mode = usage_points_for_tg_menu(db, peer.id, scope, now)
            peer_rx, peer_tx = usage_point_totals(points)
            if peer_rx > 0 or peer_tx > 0:
                active_peers += 1
            total_rx += peer_rx
            total_tx += peer_tx
            series.append(points)

        agg_points = merge_usage_points(series)
        if not agg_points:
            await message.answer(
                t("adm_no_usage_data", lang, scope=scope_label),
                reply_markup=done_markup or admin_menu(lang),
            )
            return

        chart_name = t("adm_dashboard_chart_name", lang)
        payload = {
            "peerName": chart_name,
            "scopeLabel": scope_label,
            "mode": mode,
            "timezone": settings.timezone,
            "dateCalendar": app_date_calendar(),
            "points": agg_points,
        }
        try:
            png = await render_usage_chart_png(payload)
        except Exception:
            logger.exception("Admin dashboard chart render failed")
            png = None
        if png:
            await message.answer_photo(
                BufferedInputFile(png, filename="usage.png"),
                caption=t("usage_chart_caption", lang, name=chart_name, scope=scope_label),
            )
        await message.answer(
            format_usage_total_summary(scope_label, max(active_peers, 1), total_rx, total_tx, lang),
            reply_markup=done_markup or admin_menu(lang),
        )
    finally:
        db.close()


async def _show_user_list(message: Message, *, page: int, lang: str, use_edit: bool) -> None:
    db = SessionLocal()
    try:
        users = (
            db.query(TelegramUser)
            .filter_by(is_blocked=False)
            .order_by(TelegramUser.first_name.asc(), TelegramUser.id.asc())
            .all()
        )
    finally:
        db.close()

    if not users:
        text = t("adm_no_users", lang)
        if use_edit:
            await _safe_edit_text(message, text, reply_markup=admin_menu(lang))
        else:
            await message.answer(text, reply_markup=admin_menu(lang))
        return

    total_pages = max(1, (len(users) + _USERS_PER_PAGE - 1) // _USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    slice_users = users[page * _USERS_PER_PAGE : (page + 1) * _USERS_PER_PAGE]
    labels = [_user_label(user) for user in slice_users]
    kb = admin_user_list_keyboard(
        [user.id for user in slice_users],
        labels,
        page,
        total_pages,
        lang,
    )
    text = t("adm_choose_user", lang, page=str(page + 1), total=str(total_pages))
    if use_edit:
        await _safe_edit_text(message, text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    lang = _get_admin_lang()
    await msg.answer(t("adm_menu_title", lang), reply_markup=admin_menu(lang))


@router.callback_query(lambda c: c.data and c.data.startswith("adm:"))
async def cb_admin(cb: CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer()
        return

    lang = _get_admin_lang()
    data = cb.data or ""

    if data == "adm:menu":
        await _safe_edit_text(cb.message, t("adm_menu_title", lang), reply_markup=admin_menu(lang))
        await cb.answer()
        return

    if data.startswith("adm:scope:"):
        scope = data.split(":", 2)[2]
        if scope not in _USAGE_SCOPE_LABEL_KEYS:
            await cb.answer()
            return
        await _deliver_dashboard_usage_chart(
            cb.message,
            scope,
            lang=lang,
            use_edit=True,
            done_markup=admin_menu(lang),
        )
        await cb.answer()
        return

    if data == "adm:users" or data.startswith("adm:users:p:"):
        page = 0
        if data.startswith("adm:users:p:"):
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                page = 0
        await _show_user_list(cb.message, page=page, lang=lang, use_edit=True)
        await cb.answer()
        return

    if data.startswith("adm:user:"):
        try:
            user_db_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await cb.answer()
            return
        db = SessionLocal()
        try:
            tg_user = db.get(TelegramUser, user_db_id)
        finally:
            db.close()
        if not tg_user:
            await cb.answer(t("adm_user_not_found", lang), show_alert=True)
            return
        label = _user_label(tg_user)
        await _safe_edit_text(
            cb.message,
            t("adm_user_report_title", lang, name=label),
            reply_markup=admin_user_report_menu(user_db_id, lang),
        )
        await cb.answer()
        return

    if data.startswith("adm:usr:"):
        parts = data.split(":")
        if len(parts) != 4:
            await cb.answer()
            return
        try:
            user_db_id = int(parts[2])
        except ValueError:
            await cb.answer()
            return
        scope = parts[3]
        if scope not in _USAGE_SCOPE_LABEL_KEYS:
            await cb.answer()
            return
        db = SessionLocal()
        try:
            peers = _get_peers_for_tg_user(db, user_db_id)
        finally:
            db.close()
        await _deliver_peer_usage_charts(
            cb.message,
            peers,
            scope,
            lang=lang,
            use_edit=True,
            done_markup=admin_user_report_menu(user_db_id, lang),
        )
        await cb.answer()
        return

    await cb.answer()


def register_admin_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
