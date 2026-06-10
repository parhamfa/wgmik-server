"""Tests for Telegram calendar-month picker helpers and usage window."""

from datetime import datetime, timezone

from backend.db import SessionLocal
from backend.models import Peer, Router, UsageDaily
from backend.telegram.usage_chart_image import usage_points_for_selected_calendar_month
from backend.telegram.usage_month_picker import distinct_calendar_months_with_usage


def test_distinct_calendar_months_union_across_peers(client):
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

        p1 = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="peer-1",
            public_key="pubkey-1",
            allowed_address="10.0.0.2/32",
        )
        p2 = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*2",
            name="peer-2",
            public_key="pubkey-2",
            allowed_address="10.0.0.3/32",
        )
        db.add_all([p1, p2])
        db.flush()

        db.add_all(
            [
                UsageDaily(peer_id=p1.id, day="2026-03-10", rx=1, tx=0),
                UsageDaily(peer_id=p2.id, day="2026-04-05", rx=0, tx=2),
                UsageDaily(peer_id=p1.id, day="2026-04-01", rx=0, tx=0),
            ]
        )
        db.commit()

        months = distinct_calendar_months_with_usage(db, [p1.id, p2.id], "gregorian")
        assert months == [(2026, 4), (2026, 3)]
    finally:
        db.close()


def test_distinct_calendar_months_persian_labels(client):
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
        db.add(UsageDaily(peer_id=peer.id, day="2026-04-25", rx=10, tx=5))
        db.commit()

        months = distinct_calendar_months_with_usage(db, [peer.id], "persian")
        assert months == [(1405, 2)]
    finally:
        db.close()


def test_usage_points_for_selected_calendar_month_filters_range(client):
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
                UsageDaily(peer_id=peer.id, day="2026-03-31", rx=1, tx=1),
                UsageDaily(peer_id=peer.id, day="2026-04-01", rx=100, tx=50),
                UsageDaily(peer_id=peer.id, day="2026-04-10", rx=200, tx=75),
                UsageDaily(peer_id=peer.id, day="2026-05-01", rx=9, tx=9),
            ]
        )
        db.commit()

        points, mode = usage_points_for_selected_calendar_month(
            db,
            peer.id,
            2026,
            4,
            datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        )
        assert mode == "days"
        assert points == [
            {"day": "2026-04-01", "rx": 100, "tx": 50},
            {"day": "2026-04-10", "rx": 200, "tx": 75},
        ]
    finally:
        db.close()
