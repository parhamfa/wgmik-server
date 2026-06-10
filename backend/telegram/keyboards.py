"""Inline keyboard builders for the Telegram bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .i18n import t
from .usage_month_picker import format_picker_month_button


def main_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_usage_history", lang), callback_data="menu:usage_history")],
        [InlineKeyboardButton(text=t("btn_status", lang), callback_data="menu:status")],
        [InlineKeyboardButton(text=t("btn_settings", lang), callback_data="menu:settings")],
    ])


def home_button(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def empty_inline_keyboard() -> InlineKeyboardMarkup:
    """Remove inline buttons from an edited message (Telegram requires an explicit empty keyboard)."""
    return InlineKeyboardMarkup(inline_keyboard=[])


def usage_history_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data="usage:today"),
            InlineKeyboardButton(text=t("btn_this_month", lang), callback_data="usage:month"),
            InlineKeyboardButton(text=t("btn_all_time", lang), callback_data="usage:alltime"),
        ],
        [InlineKeyboardButton(text=t("btn_pick_month", lang), callback_data="usagepick:years")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def usagepick_year_page_keyboard(
    lang: str,
    years: list[int],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(years), 2):
        row = [
            InlineKeyboardButton(
                text=str(years[i]),
                callback_data=f"usagepick:y:{years[i]}",
            )
        ]
        if i + 1 < len(years):
            row.append(
                InlineKeyboardButton(
                    text=str(years[i + 1]),
                    callback_data=f"usagepick:y:{years[i + 1]}",
                )
            )
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("usagepick_prev", lang),
                callback_data=f"usagepick:py:{page - 1}",
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=t("usagepick_next", lang),
                callback_data=f"usagepick:py:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:usage_history")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def usagepick_months_for_year_keyboard(
    lang: str,
    year: int,
    months_present: list[int],
    calendar: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for m in sorted(months_present):
        label = format_picker_month_button(year, m, calendar)
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"usagepick:m:{year}:{m}",
            )
        )
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="usagepick:years")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_language", lang), callback_data="settings:language")],
        [InlineKeyboardButton(text=t("btn_notifications", lang), callback_data="settings:notifications")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def language_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="English", callback_data="settings:lang:en")],
        [InlineKeyboardButton(text="فارسی", callback_data="settings:lang:fa")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:settings")],
    ])


def notifications_menu(items: list[tuple[str, str]], lang: str = "en") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"settings:notif:{event_type}")] for event_type, label in items]
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data="adm:scope:today"),
            InlineKeyboardButton(text=t("btn_this_month", lang), callback_data="adm:scope:month"),
            InlineKeyboardButton(text=t("btn_all_time", lang), callback_data="adm:scope:alltime"),
        ],
        [InlineKeyboardButton(text=t("adm_btn_user_report", lang), callback_data="adm:users")],
    ])


def admin_user_list_keyboard(
    user_ids: list[int],
    labels: list[str],
    page: int,
    total_pages: int,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user_id, label in zip(user_ids, labels):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"adm:user:{user_id}"),
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("usagepick_prev", lang),
                callback_data=f"adm:users:p:{page - 1}",
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=t("usagepick_next", lang),
                callback_data=f"adm:users:p:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_report_menu(user_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data=f"adm:usr:{user_id}:today"),
            InlineKeyboardButton(text=t("btn_this_month", lang), callback_data=f"adm:usr:{user_id}:month"),
            InlineKeyboardButton(text=t("btn_all_time", lang), callback_data=f"adm:usr:{user_id}:alltime"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="adm:users")],
    ])
