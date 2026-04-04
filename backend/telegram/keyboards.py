"""Inline keyboard builders for the Telegram bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .i18n import t


def main_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_my_peers", lang), callback_data="menu:peers"),
            InlineKeyboardButton(text=t("btn_usage", lang), callback_data="menu:usage"),
        ],
        [
            InlineKeyboardButton(text=t("btn_fair_usage", lang), callback_data="menu:fair_usage"),
            InlineKeyboardButton(text=t("btn_settings", lang), callback_data="menu:settings"),
        ],
    ])


def back_button(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def usage_scope_selector(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data="usage:today"),
            InlineKeyboardButton(text=t("btn_this_week", lang), callback_data="usage:week"),
            InlineKeyboardButton(text=t("btn_this_month", lang), callback_data="usage:month"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def settings_menu(lang: str = "en") -> InlineKeyboardMarkup:
    other = "fa" if lang == "en" else "en"
    label = "فارسی" if other == "fa" else "English"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Language: {label}", callback_data=f"settings:lang:{other}")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


def peer_list_keyboard(peers: list[tuple[int, str]], lang: str = "en") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"peer:{pid}")] for pid, name in peers]
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
