import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import backend.scheduler as scheduler_module
from backend.db import SessionLocal
from backend.models import Action, Peer, Router, SettingsKV, UsageDaily, UsageHold, UsageMinute, UsageMonthly, UsageSample
from backend.settings import settings
from backend.usage_guard import run_one_sided_guard
from backend.usage_holds import decide_hold_by_id
from backend.usage_selfcheck import run_usage_selfcheck

GIB = 1024 ** 3
MIB = 1024 ** 2


def _seed_peer(*, name: str, first_ts: datetime, rx: int, tx: int) -> int:
    db = SessionLocal()
    try:
        router = Router(
            name="Router A",
            host="10.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-a",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name=name,
            public_key=f"pub-{name}",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()
        db.add(UsageSample(peer_id=peer.id, ts=first_ts.replace(tzinfo=None), rx=rx, tx=tx, endpoint=""))
        db.commit()
        return peer.id
    finally:
        db.close()


def _poll(monkeypatch, name: str, readings: list[tuple[datetime, int, int]]) -> list[tuple[int, str]]:
    """Run the real poller once per (time, rx, tx) reading against a stub router."""
    state = {"rx": 0, "tx": 0}
    notices: list[tuple[int, str]] = []

    class FakeDateTime:
        current = readings[0][0]

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    class StubClient:
        def list_all_wireguard_peers(self):
            return [
                SimpleNamespace(
                    interface="wg0",
                    public_key=f"pub-{name}",
                    ros_id="*1",
                    name=name,
                    allowed_address="10.0.0.10/32",
                    disabled=False,
                    rx_bytes=state["rx"],
                    tx_bytes=state["tx"],
                    endpoint="",
                )
            ]

        def set_peer_disabled(self, iface, ros_id, disabled):
            return None

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    monkeypatch.setattr(scheduler_module, "datetime", FakeDateTime)
    monkeypatch.setattr(scheduler_module, "notify_holds_created", lambda items: notices.extend(items))
    for ts, rx, tx in readings:
        FakeDateTime.current = ts
        state["rx"], state["tx"] = rx, tx
        scheduler_module._poll_once()
    return notices


def _totals(peer_id: int) -> dict[str, tuple[int, int]]:
    db = SessionLocal()
    try:
        minutes = db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).all()
        daily = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id).all()
        monthly = db.query(UsageMonthly).filter(UsageMonthly.peer_id == peer_id).all()
        return {
            "minute": (sum(r.rx for r in minutes), sum(r.tx for r in minutes)),
            "daily": (sum(r.rx for r in daily), sum(r.tx for r in daily)),
            "monthly": (sum(r.rx for r in monthly), sum(r.tx for r in monthly)),
            "daily_keys": tuple(sorted(r.day for r in daily)),
            "monthly_keys": tuple(sorted(r.month_key for r in monthly)),
        }
    finally:
        db.close()


def _actions(peer_id: int, action: str) -> list[Action]:
    db = SessionLocal()
    try:
        return db.query(Action).filter(Action.peer_id == peer_id, Action.action == action).order_by(Action.id).all()
    finally:
        db.close()


def _hold_count() -> int:
    db = SessionLocal()
    try:
        return db.query(UsageHold).count()
    finally:
        db.close()


def test_startup_writes_usage_counting_version(client):
    db = SessionLocal()
    try:
        assert db.get(SettingsKV, "usage_counting_version").value == "2"
    finally:
        db.close()


def test_reset_mid_day_keeps_counting_without_hold(client, monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Asia/Tehran")
    base = datetime(2026, 6, 16, 8, 0, 0, tzinfo=timezone.utc)
    peer_id = _seed_peer(name="reset-peer", first_ts=base - timedelta(seconds=60), rx=136_600_000, tx=6_830_000_000)

    # Router script disables/enables the peer: both counters fall to 0, then 27 GB
    # of download (and 2% upload) arrives over the next six minutes.
    readings = [(base, 0, 0)] + [
        (base + timedelta(minutes=k), k * 90_000_000, k * 4_500_000_000) for k in range(1, 7)
    ]
    notices = _poll(monkeypatch, "reset-peer", readings)

    db = SessionLocal()
    try:
        assert run_one_sided_guard(db, base + timedelta(minutes=7, seconds=10)) == []
        db.commit()
    finally:
        db.close()

    totals = _totals(peer_id)
    assert totals["minute"] == (540_000_000, 27_000_000_000)
    assert totals["daily"] == (540_000_000, 27_000_000_000)
    assert totals["monthly"] == (540_000_000, 27_000_000_000)
    assert totals["daily_keys"] == ("2026-06-16",)
    assert notices == []
    assert _hold_count() == 0
    assert [a.note for a in _actions(peer_id, "counter_reset")] == [
        "Detected counter reset: rx 136600000->0, tx 6830000000->0"
    ]


def _june16_scenario(client, monkeypatch) -> tuple[int, datetime, dict]:
    """Download climbs ~7.3 GiB in ten normal-looking minutes while upload moves kilobytes."""
    monkeypatch.setattr(settings, "timezone", "Asia/Tehran")
    base = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
    peer_id = _seed_peer(name="sepehr-web-surfing", first_ts=base - timedelta(seconds=60), rx=1_000_000, tx=0)
    readings = [
        (base + timedelta(minutes=k), 1_000_000 + (k + 1) * 5_000, (k + 1) * 750 * MIB) for k in range(10)
    ]
    assert _poll(monkeypatch, "sepehr-web-surfing", readings) == []
    before = _totals(peer_id)
    assert before["minute"] == (50_000, 7_500 * MIB)
    return peer_id, base, before


def _run_guard(now_utc: datetime) -> list[tuple[int, str]]:
    db = SessionLocal()
    try:
        notices = run_one_sided_guard(db, now_utc)
        db.commit()
        return notices
    finally:
        db.close()


def test_one_sided_guard_holds_june16_pattern_and_approve_restores_exactly(client, monkeypatch):
    peer_id, base, before = _june16_scenario(client, monkeypatch)
    # Keep 2026 minute rollups inside the retention window so approve restores them.
    db = SessionLocal()
    try:
        db.add(SettingsKV(key="minute_rollup_retention_days", value="3650"))
        db.commit()
    finally:
        db.close()

    notices = _run_guard(base + timedelta(minutes=10, seconds=30))
    assert len(notices) == 1
    hold_id, note = notices[0]
    assert "download" in note and "sepehr-web-surfing" in note

    db = SessionLocal()
    try:
        hold = db.get(UsageHold, hold_id)
        assert (hold.direction, hold.reason, hold.status) == ("tx", "one_sided", "pending")
        assert hold.bytes == 7_500 * MIB
        assert len(json.loads(hold.minutes_json)) == 10
    finally:
        db.close()

    held = _totals(peer_id)
    assert held["minute"] == (50_000, 0)
    assert held["daily"] == (50_000, 0)
    assert held["monthly"] == (50_000, 0)
    assert len(_actions(peer_id, "usage_held")) == 1

    # Held minutes are never held twice, and held bytes do not trip the self-check.
    assert _run_guard(base + timedelta(minutes=12)) == []
    db = SessionLocal()
    try:
        summary = run_usage_selfcheck(db, now_utc=base + timedelta(minutes=12))
        db.commit()
    finally:
        db.close()
    assert summary["mismatches"] == []

    listed = client.get("/api/admin/usage_holds?status=pending")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [hold_id]
    assert listed.json()[0]["peer_name"] == "sepehr-web-surfing"

    approved = client.post(f"/api/admin/usage_holds/{hold_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert _totals(peer_id) == before

    again = client.post(f"/api/admin/usage_holds/{hold_id}/approve")
    assert again.status_code == 200, again.text
    discard_after = client.post(f"/api/admin/usage_holds/{hold_id}/discard")
    assert discard_after.json()["status"] == "approved"
    assert _totals(peer_id) == before
    assert len(_actions(peer_id, "usage_hold_approved")) == 1
    assert _actions(peer_id, "usage_hold_discarded") == []

    # Approved minutes stay counted and are not held again.
    assert _run_guard(base + timedelta(minutes=14)) == []
    assert client.post("/api/admin/usage_holds/99999/approve").status_code == 404


def test_one_sided_hold_discard_keeps_bytes_out(client, monkeypatch):
    peer_id, base, _before = _june16_scenario(client, monkeypatch)
    hold_id, _note = _run_guard(base + timedelta(minutes=10, seconds=30))[0]

    first = decide_hold_by_id(hold_id, approve=False, source="Telegram admin")
    assert (first.status, first.changed) == ("discarded", True)
    second = decide_hold_by_id(hold_id, approve=True, source="Telegram admin")
    assert (second.status, second.changed) == ("discarded", False)

    totals = _totals(peer_id)
    assert totals["minute"] == (50_000, 0)
    assert totals["daily"] == (50_000, 0)
    assert totals["monthly"] == (50_000, 0)
    assert len(_actions(peer_id, "usage_hold_discarded")) == 1
    assert _actions(peer_id, "usage_hold_approved") == []
    assert decide_hold_by_id(99999, approve=True, source="x").status == "missing"


def test_approve_outside_minute_retention_restores_daily_and_monthly_only(client, monkeypatch):
    peer_id, base, before = _june16_scenario(client, monkeypatch)
    hold_id, _note = _run_guard(base + timedelta(minutes=10, seconds=30))[0]

    # Default 90-day minute retention: June 2026 minutes are already past it.
    decision = decide_hold_by_id(hold_id, approve=True, source="Telegram admin")
    assert decision.changed is True

    totals = _totals(peer_id)
    assert totals["minute"] == (50_000, 0)
    assert totals["daily"] == before["daily"]
    assert totals["monthly"] == before["monthly"]


def test_implausible_jump_is_held_but_long_gap_is_counted(client, monkeypatch):
    base = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    peer_id = _seed_peer(name="jumpy", first_ts=base - timedelta(seconds=5), rx=1_000, tx=1_000)

    # 10 GB in 5 seconds (16 Gbit/s) is above the 1000 Mbit/s cap.
    notices = _poll(
        monkeypatch,
        "jumpy",
        [
            (base, 2_000, 10_000_001_000),
            (base + timedelta(seconds=60), 3_000, 10_000_101_000),
        ],
    )
    assert len(notices) == 1
    totals = _totals(peer_id)
    assert totals["minute"] == (2_000, 100_000)
    assert totals["daily"] == (2_000, 100_000)

    db = SessionLocal()
    try:
        hold = db.query(UsageHold).filter(UsageHold.peer_id == peer_id).one()
        assert (hold.direction, hold.reason, hold.status, hold.bytes) == ("tx", "implausible_jump", "pending", 10_000_000_000)
        assert json.loads(hold.minutes_json) == {"2026-06-16 12:00:00": 10_000_000_000}
        assert hold.id == notices[0][0]
    finally:
        db.close()
    assert len(_actions(peer_id, "usage_held")) == 1


def test_large_delta_after_long_polling_gap_is_not_held(client, monkeypatch):
    base = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    peer_id = _seed_peer(name="gap", first_ts=base - timedelta(hours=3), rx=1_000, tx=1_000)

    # 100 GB over three hours is about 74 Mbit/s: real traffic, counted.
    notices = _poll(monkeypatch, "gap", [(base, 5_000_001_000, 100_000_001_000)])

    assert notices == []
    assert _hold_count() == 0
    assert _totals(peer_id)["minute"] == (5_000_000_000, 100_000_000_000)


def test_max_peer_rate_setting_lowers_the_cap(client, monkeypatch):
    base = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    peer_id = _seed_peer(name="capped", first_ts=base - timedelta(seconds=60), rx=0, tx=0)
    db = SessionLocal()
    try:
        db.add(SettingsKV(key="usage_max_peer_rate_mbps", value="10"))
        db.commit()
    finally:
        db.close()

    # 100 MB in 60 s is ~13 Mbit/s: above a 10 Mbit/s cap.
    notices = _poll(monkeypatch, "capped", [(base, 1_000_000, 100_000_000)])

    assert len(notices) == 1
    assert _totals(peer_id)["minute"] == (1_000_000, 0)
