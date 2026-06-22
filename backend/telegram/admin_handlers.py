"""Admin-only Telegram bot handlers (gated by tg_admin_chat_id)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..calendar_utils import app_date_calendar, format_app_datetime
from ..db import SessionLocal
from ..models import Peer, SettingsKV, TelegramBroadcast, TelegramBroadcastRecipient, TelegramPeerBinding, TelegramUser
from ..settings import settings
from .formatters import format_usage_total_summary, usage_point_totals
from .i18n import t
from .keyboards import (
    admin_menu,
    admin_outbox_detail_keyboard,
    admin_outbox_list_keyboard,
    admin_usagepick_months_for_year_keyboard,
    admin_usagepick_year_page_keyboard,
    admin_user_list_keyboard,
    admin_user_report_menu,
)
from .usage_chart_image import (
    merge_usage_points,
    render_usage_chart_png,
    usage_points_for_selected_calendar_month,
    usage_points_for_tg_menu,
)
from .usage_month_picker import (
    YEARS_PER_PAGE,
    distinct_calendar_months_with_usage,
    format_picker_month_scope_label,
)

logger = logging.getLogger("wgmik.telegram.admin_handlers")

router = Router()

_USERS_PER_PAGE = 8
_OUTBOX_PER_PAGE = 6

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


def _usagepick_year_page_slice(
    months: list[tuple[int, int]], page: int
) -> tuple[list[int], int, int]:
    years = sorted({y for y, _ in months}, reverse=True)
    total_pages = max(1, (len(years) + YEARS_PER_PAGE - 1) // YEARS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * YEARS_PER_PAGE
    return years[start : start + YEARS_PER_PAGE], page, total_pages


def _callback_prefix_for_calendar_target(target: str, user_db_id: int | None = None) -> str:
    return "admcal:a" if target == "all" else f"admcal:u:{int(user_db_id or 0)}"


def _back_callback_for_calendar_target(target: str, user_db_id: int | None = None) -> str:
    return "adm:menu" if target == "all" else f"adm:user:{int(user_db_id or 0)}"


def _done_markup_for_calendar_target(target: str, user_db_id: int | None, lang: str):
    return admin_menu(lang) if target == "all" else admin_user_report_menu(int(user_db_id or 0), lang)


def _months_with_usage_for_peers(db, peers: list[Peer]) -> list[tuple[int, int]]:
    return distinct_calendar_months_with_usage(db, [peer.id for peer in peers], app_date_calendar())


async def _show_admin_calendar_years(
    message: Message,
    *,
    target: str,
    user_db_id: int | None,
    page: int,
    lang: str,
    use_edit: bool,
) -> None:
    db = SessionLocal()
    try:
        peers = _get_all_peers(db) if target == "all" else _get_peers_for_tg_user(db, int(user_db_id or 0))
        months = _months_with_usage_for_peers(db, peers)
    finally:
        db.close()

    done_markup = _done_markup_for_calendar_target(target, user_db_id, lang)
    if not peers:
        text = t("adm_no_peers", lang)
        if use_edit:
            await _safe_edit_text(message, text, reply_markup=done_markup)
        else:
            await message.answer(text, reply_markup=done_markup)
        return
    if not months:
        text = t("usagepick_no_months", lang)
        if use_edit:
            await _safe_edit_text(message, text, reply_markup=done_markup)
        else:
            await message.answer(text, reply_markup=done_markup)
        return

    slice_years, page, total_pages = _usagepick_year_page_slice(months, page)
    kb = admin_usagepick_year_page_keyboard(
        lang,
        _callback_prefix_for_calendar_target(target, user_db_id),
        slice_years,
        page,
        total_pages,
        _back_callback_for_calendar_target(target, user_db_id),
    )
    if use_edit:
        await _safe_edit_text(message, t("usagepick_choose_year", lang), reply_markup=kb)
    else:
        await message.answer(t("usagepick_choose_year", lang), reply_markup=kb)


async def _show_admin_calendar_months(
    message: Message,
    *,
    target: str,
    user_db_id: int | None,
    year: int,
    lang: str,
) -> None:
    db = SessionLocal()
    try:
        peers = _get_all_peers(db) if target == "all" else _get_peers_for_tg_user(db, int(user_db_id or 0))
        months = _months_with_usage_for_peers(db, peers)
    finally:
        db.close()

    months_in_year = sorted({month for y, month in months if y == year})
    if not months_in_year:
        await _show_admin_calendar_years(
            message,
            target=target,
            user_db_id=user_db_id,
            page=0,
            lang=lang,
            use_edit=True,
        )
        return

    kb = admin_usagepick_months_for_year_keyboard(
        lang,
        _callback_prefix_for_calendar_target(target, user_db_id),
        year,
        months_in_year,
        app_date_calendar(),
    )
    await _safe_edit_text(
        message,
        t("usagepick_choose_month", lang, year=str(year)),
        reply_markup=kb,
    )


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


async def _deliver_peer_usage_charts_for_month(
    message: Message,
    peers: list[Peer],
    cal_y: int,
    cal_m: int,
    *,
    lang: str,
    done_markup=None,
) -> None:
    if not peers:
        await _safe_edit_text(message, t("adm_no_peers", lang), reply_markup=done_markup or admin_menu(lang))
        return

    scope_label = format_picker_month_scope_label(cal_y, cal_m, app_date_calendar())
    await _safe_edit_text(
        message,
        t("adm_usage_sending", lang, scope=scope_label),
        reply_markup=done_markup,
    )

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    sent_any = False
    try:
        total_rx = 0
        total_tx = 0
        total_peers = 0
        for peer in peers:
            points, mode = usage_points_for_selected_calendar_month(db, peer.id, cal_y, cal_m, now)
            if not points:
                continue
            peer_rx, peer_tx = usage_point_totals(points)
            total_rx += peer_rx
            total_tx += peer_tx
            total_peers += 1
            sent_any = True
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
                logger.exception("Admin picked-month chart failed for peer %s", peer.id)
                continue
            await message.answer_photo(
                BufferedInputFile(png, filename="usage.png"),
                caption=t("usage_chart_caption", lang, name=label, scope=scope_label),
            )
        if total_peers > 1:
            await message.answer(
                format_usage_total_summary(scope_label, total_peers, total_rx, total_tx, lang)
            )
    finally:
        db.close()

    if not sent_any:
        await message.answer(t("adm_no_usage_data", lang, scope=scope_label))
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


async def _deliver_dashboard_usage_chart_for_month(
    message: Message,
    cal_y: int,
    cal_m: int,
    *,
    lang: str,
    done_markup=None,
) -> None:
    scope_label = format_picker_month_scope_label(cal_y, cal_m, app_date_calendar())
    await _safe_edit_text(message, t("adm_usage_sending", lang, scope=scope_label))

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
            points, mode = usage_points_for_selected_calendar_month(db, peer.id, cal_y, cal_m, now)
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
            logger.exception("Admin picked-month dashboard chart render failed")
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


def _broadcast_button_label(broadcast: TelegramBroadcast) -> str:
    return (
        f"#{broadcast.id} {broadcast.status} "
        f"{broadcast.sent_count}/{broadcast.total_count} sent, "
        f"{broadcast.acknowledged_count} ack"
    )


async def _show_outbox_list(message: Message, *, page: int, lang: str, use_edit: bool) -> None:
    db = SessionLocal()
    try:
        total = db.query(TelegramBroadcast).count()
        total_pages = max(1, (total + _OUTBOX_PER_PAGE - 1) // _OUTBOX_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        broadcasts = (
            db.query(TelegramBroadcast)
            .order_by(TelegramBroadcast.created_at.desc(), TelegramBroadcast.id.desc())
            .offset(page * _OUTBOX_PER_PAGE)
            .limit(_OUTBOX_PER_PAGE)
            .all()
        )
        items = [(b.id, _broadcast_button_label(b)) for b in broadcasts]
    finally:
        db.close()

    if not items:
        text = t("adm_outbox_empty", lang)
        markup = admin_menu(lang)
    else:
        text = t("adm_outbox_title", lang)
        markup = admin_outbox_list_keyboard(items, page, total_pages, lang)
    if use_edit:
        await _safe_edit_text(message, text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _show_outbox_detail(message: Message, *, broadcast_id: int, lang: str) -> None:
    db = SessionLocal()
    try:
        broadcast = db.get(TelegramBroadcast, broadcast_id)
        if not broadcast:
            await _safe_edit_text(message, t("broadcast_ack_unavailable", lang), reply_markup=admin_outbox_detail_keyboard(lang))
            return
        created = format_app_datetime(broadcast.created_at, calendar=app_date_calendar())
        text = t(
            "adm_outbox_detail",
            lang,
            id=str(broadcast.id),
            status=broadcast.status,
            total=str(broadcast.total_count),
            sent=str(broadcast.sent_count),
            failed=str(broadcast.failed_count),
            ack=str(broadcast.acknowledged_count),
            created=created,
        )
        failed = (
            db.query(TelegramBroadcastRecipient)
            .filter_by(broadcast_id=broadcast.id, status="failed")
            .order_by(TelegramBroadcastRecipient.id.asc())
            .limit(5)
            .all()
        )
        if failed:
            items = "\n".join(
                f"- {row.display_name or row.chat_id}: {row.error_message or row.error_code}"
                for row in failed
            )
            text = text + "\n\n" + t("adm_outbox_failed_examples", lang, items=items)
    finally:
        db.close()

    await _safe_edit_text(message, text, reply_markup=admin_outbox_detail_keyboard(lang))


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

    if data == "adm:outbox" or data.startswith("adm:outbox:p:"):
        page = 0
        if data.startswith("adm:outbox:p:"):
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                page = 0
        await _show_outbox_list(cb.message, page=page, lang=lang, use_edit=True)
        await cb.answer()
        return

    if data.startswith("adm:out:"):
        try:
            broadcast_id = int(data.split(":")[2])
        except (IndexError, ValueError):
            await cb.answer()
            return
        await _show_outbox_detail(cb.message, broadcast_id=broadcast_id, lang=lang)
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


@router.callback_query(lambda c: c.data and c.data.startswith("admcal:"))
async def cb_admin_calendar(cb: CallbackQuery):
    if not _is_admin(cb.from_user.id):
        await cb.answer()
        return

    lang = _get_admin_lang()
    data = cb.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return

    target = "all" if parts[1] == "a" else "user"
    user_db_id: int | None = None
    action_idx = 2
    if target == "user":
        if len(parts) < 4:
            await cb.answer()
            return
        try:
            user_db_id = int(parts[2])
        except ValueError:
            await cb.answer()
            return
        action_idx = 3

    action = parts[action_idx]

    if action == "years":
        await _show_admin_calendar_years(
            cb.message,
            target=target,
            user_db_id=user_db_id,
            page=0,
            lang=lang,
            use_edit=True,
        )
        await cb.answer()
        return

    if action == "py":
        try:
            page = int(parts[action_idx + 1])
        except (IndexError, ValueError):
            await cb.answer()
            return
        await _show_admin_calendar_years(
            cb.message,
            target=target,
            user_db_id=user_db_id,
            page=page,
            lang=lang,
            use_edit=True,
        )
        await cb.answer()
        return

    if action == "y":
        try:
            year = int(parts[action_idx + 1])
        except (IndexError, ValueError):
            await cb.answer()
            return
        await _show_admin_calendar_months(
            cb.message,
            target=target,
            user_db_id=user_db_id,
            year=year,
            lang=lang,
        )
        await cb.answer()
        return

    if action == "m":
        try:
            cal_y = int(parts[action_idx + 1])
            cal_m = int(parts[action_idx + 2])
        except (IndexError, ValueError):
            await cb.answer()
            return
        if target == "all":
            await _deliver_dashboard_usage_chart_for_month(
                cb.message,
                cal_y,
                cal_m,
                lang=lang,
                done_markup=admin_menu(lang),
            )
        else:
            db = SessionLocal()
            try:
                peers = _get_peers_for_tg_user(db, int(user_db_id or 0))
            finally:
                db.close()
            await _deliver_peer_usage_charts_for_month(
                cb.message,
                peers,
                cal_y,
                cal_m,
                lang=lang,
                done_markup=admin_user_report_menu(int(user_db_id or 0), lang),
            )
        await cb.answer()
        return

    await cb.answer()


def register_admin_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
