"""Inline keyboard builders for the Telegram bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .i18n import t


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


def usage_history_menu(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data="usage:today"),
            InlineKeyboardButton(text=t("btn_this_week", lang), callback_data="usage:week"),
            InlineKeyboardButton(text=t("btn_this_month", lang), callback_data="usage:month"),
        ],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:main")],
    ])


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
