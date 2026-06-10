import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.db import SessionLocal
from backend.models import Peer, Router, UsageDaily
from backend.telegram.formatters import format_usage_total_summary, usage_point_totals
from backend.telegram.usage_chart_image import usage_points_for_tg_menu


@dataclass
class _FakeInlineKeyboardButton:
    text: str
    callback_data: str


@dataclass
class _FakeInlineKeyboardMarkup:
    inline_keyboard: list[list[_FakeInlineKeyboardButton]]


@dataclass
class _FakeBotCommand:
    command: str
    description: str


_fake_aiogram_types = types.ModuleType("aiogram.types")
_fake_aiogram_types.InlineKeyboardButton = _FakeInlineKeyboardButton
_fake_aiogram_types.InlineKeyboardMarkup = _FakeInlineKeyboardMarkup
_fake_aiogram_types.BotCommand = _FakeBotCommand
sys.modules.setdefault("aiogram", types.ModuleType("aiogram"))
sys.modules["aiogram.types"] = _fake_aiogram_types

from backend.telegram.bot import _bot_commands_for_language
from backend.telegram.keyboards import usage_history_menu


def test_usage_history_menu_uses_today_month_alltime():
    buttons = [
        button.callback_data
        for row in usage_history_menu("en").inline_keyboard
        for button in row
    ]

    assert "usage:today" in buttons
    assert "usage:month" in buttons
    assert "usage:alltime" in buttons
    assert "usage:week" not in buttons
    assert "usagepick:years" in buttons
    assert "usage:month" != "usagepick:m:2026:4"


def test_usagepick_callback_data_stays_short():
    assert len("usagepick:m:1405:12") <= 64
    assert len("usagepick:py:9") <= 64


def test_bot_commands_include_alltime_and_exclude_weekly():
    commands = [command.command for command in _bot_commands_for_language("en")]

    assert commands == [
        "start",
        "home",
        "today",
        "monthly",
        "alltime",
        "calendar",
        "fair",
        "settings",
    ]
    assert "weekly" not in commands


def test_usage_points_for_tg_menu_alltime_returns_full_daily_history(client):
    db = SessionLocal()
    try:
        router = Router(
            name="r1",
            host="127.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
        )
        db.add(router)
        db.flush()

        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="peer-1",
            public_key="pubkey-1",
            allowed_address="10.0.0.2/32",
        )
        db.add(peer)
        db.flush()

        db.add_all(
            [
                UsageDaily(peer_id=peer.id, day="2026-04-01", rx=100, tx=50),
                UsageDaily(peer_id=peer.id, day="2026-04-10", rx=200, tx=75),
                UsageDaily(peer_id=peer.id, day="2026-04-22", rx=300, tx=125),
            ]
        )
        db.commit()

        points, mode = usage_points_for_tg_menu(
            db,
            peer.id,
            "alltime",
            datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        )

        assert mode == "days"
        assert points == [
            {"day": "2026-04-01", "rx": 100, "tx": 50},
            {"day": "2026-04-10", "rx": 200, "tx": 75},
            {"day": "2026-04-22", "rx": 300, "tx": 125},
        ]
    finally:
        db.close()


def test_usage_point_totals_sum_rx_and_tx():
    rx, tx = usage_point_totals(
        [
            {"day": "2026-04-01", "rx": 100, "tx": 50},
            {"day": "2026-04-02", "rx": 200, "tx": 75},
            {"day": "2026-04-03", "rx": 0, "tx": 25},
        ]
    )

    assert rx == 300
    assert tx == 150


def test_format_usage_total_summary_matches_chart_direction_labels():
    text = format_usage_total_summary(
        "All Time",
        peer_count=3,
        rx_bytes=300 * 1024 * 1024,
        tx_bytes=150 * 1024 * 1024,
        lang="en",
    )

    assert "Total across 3 connections (All Time)" in text
    assert "⬇ Download: 150 MB" in text
    assert "⬆ Upload: 300 MB" in text
    assert "∑ Total: 450 MB" in text
