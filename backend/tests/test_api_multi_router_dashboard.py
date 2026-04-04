from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import backend.scheduler as scheduler_module
from backend.db import SessionLocal
from backend.models import Peer, Router, SettingsKV, UsageDaily, UsageMinute, UsageSample


def seed_router_data():
    db = SessionLocal()
    try:
        router1 = Router(
            name="Router A",
            host="10.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-a",
            tls_verify=True,
        )
        router2 = Router(
            name="Router B",
            host="10.0.0.2",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-b",
            tls_verify=True,
        )
        db.add_all([router1, router2])
        db.flush()

        peer1 = Peer(
            router_id=router1.id,
            interface="wg0",
            ros_id="*1",
            name="peer-a",
            public_key="pub-a",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        peer2 = Peer(
            router_id=router2.id,
            interface="wg0",
            ros_id="*2",
            name="peer-b",
            public_key="pub-b",
            allowed_address="10.0.0.11/32",
            disabled=False,
            selected=True,
        )
        db.add_all([peer1, peer2])
        db.flush()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        db.add_all(
            [
                UsageDaily(peer_id=peer1.id, day=today, rx=100, tx=50),
                UsageDaily(peer_id=peer2.id, day=today, rx=200, tx=75),
            ]
        )

        now = datetime.now(timezone.utc).replace(microsecond=0)
        db.add_all(
            [
                UsageSample(peer_id=peer1.id, ts=(now - timedelta(minutes=2)).replace(tzinfo=None), rx=1000, tx=400),
                UsageSample(peer_id=peer1.id, ts=(now - timedelta(minutes=1)).replace(tzinfo=None), rx=1300, tx=550),
                UsageSample(peer_id=peer2.id, ts=(now - timedelta(minutes=2)).replace(tzinfo=None), rx=2000, tx=500),
                UsageSample(peer_id=peer2.id, ts=(now - timedelta(minutes=1)).replace(tzinfo=None), rx=2600, tx=900),
            ]
        )
        db.commit()
        return {
            "router1_id": router1.id,
            "router2_id": router2.id,
            "peer1_id": peer1.id,
            "peer2_id": peer2.id,
        }
    finally:
        db.close()


def seed_minute_data():
    db = SessionLocal()
    try:
        router1 = Router(
            name="Router A",
            host="10.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-a",
            tls_verify=True,
        )
        router2 = Router(
            name="Router B",
            host="10.0.0.2",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-b",
            tls_verify=True,
        )
        db.add_all([router1, router2])
        db.flush()

        peer1 = Peer(
            router_id=router1.id,
            interface="wg0",
            ros_id="*1",
            name="peer-a",
            public_key="pub-a",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        peer2 = Peer(
            router_id=router2.id,
            interface="wg0",
            ros_id="*2",
            name="peer-b",
            public_key="pub-b",
            allowed_address="10.0.0.11/32",
            disabled=False,
            selected=True,
        )
        db.add_all([peer1, peer2])
        db.flush()

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
        db.add_all(
            [
                UsageMinute(peer_id=peer1.id, minute_ts=now - timedelta(minutes=2), rx=120, tx=60),
                UsageMinute(peer_id=peer1.id, minute_ts=now - timedelta(minutes=1), rx=180, tx=90),
                UsageMinute(peer_id=peer2.id, minute_ts=now - timedelta(minutes=2), rx=300, tx=100),
                UsageMinute(peer_id=peer2.id, minute_ts=now - timedelta(minutes=1), rx=420, tx=200),
            ]
        )
        db.commit()
        return {
            "router1_id": router1.id,
            "router2_id": router2.id,
            "peer1_id": peer1.id,
            "peer2_id": peer2.id,
        }
    finally:
        db.close()


def test_settings_round_trip_dashboard_scope(client):
    payload = client.get("/api/settings").json()
    payload["dashboard_router_scope"] = "selected"
    payload["dashboard_selected_router_ids"] = [2, 1, 2]
    payload["raw_sample_retention_hours"] = 48
    payload["minute_rollup_retention_days"] = 120
    payload["daily_rollup_retention_days"] = 365

    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["dashboard_router_scope"] == "selected"
    assert data["dashboard_selected_router_ids"] == [2, 1]
    assert data["raw_sample_retention_hours"] == 48
    assert data["minute_rollup_retention_days"] == 120
    assert data["daily_rollup_retention_days"] == 365


def test_settings_invalid_selected_router_json_sanitized(client):
    db = SessionLocal()
    try:
        db.add(SettingsKV(key="dashboard_selected_router_ids", value="{bad json"))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["dashboard_selected_router_ids"] == []


def test_peers_router_ids_take_precedence_over_router_id(client):
    seeded = seed_router_data()

    response = client.get(
        f"/api/peers?selected_only=true&router_id={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert response.status_code == 200, response.text

    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["router_id"] == seeded["router2_id"]


def test_summary_endpoints_support_router_ids(client):
    seeded = seed_router_data()

    month = client.get(f"/api/summary/month?router_ids={seeded['router2_id']}")
    assert month.status_code == 200, month.text
    month_rows = [row for row in month.json() if (row["rx"] or row["tx"])]
    assert len(month_rows) == 1
    assert month_rows[0]["rx"] == 200
    assert month_rows[0]["tx"] == 75

    peers = client.get(f"/api/summary/peers?days=1&router_ids={seeded['router2_id']}")
    assert peers.status_code == 200, peers.text
    peer_rows = peers.json()
    assert len(peer_rows) == 1
    assert peer_rows[0]["peer_id"] == seeded["peer2_id"]
    assert peer_rows[0]["rx"] == 200
    assert peer_rows[0]["tx"] == 75

    raw = client.get(f"/api/summary/raw?seconds=600&router_ids={seeded['router2_id']}&interval=60")
    assert raw.status_code == 200, raw.text
    raw_rows = raw.json()
    assert len(raw_rows) == 1
    assert raw_rows[0]["rx"] == 600
    assert raw_rows[0]["tx"] == 400


def test_grouped_summary_endpoints_return_router_scoped_rows(client):
    seeded = seed_router_data()

    month = client.get(
        f"/api/summary/month/by_router?router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert month.status_code == 200, month.text
    month_rows = [row for row in month.json() if (row["rx"] or row["tx"])]
    assert {row["router_id"] for row in month_rows} == {seeded["router1_id"], seeded["router2_id"]}

    raw = client.get(
        f"/api/summary/raw/by_router?seconds=600&interval=60&router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert raw.status_code == 200, raw.text
    raw_rows = raw.json()
    assert len(raw_rows) == 2
    by_router = {row["router_id"]: row for row in raw_rows}
    assert by_router[seeded["router1_id"]]["rx"] == 300
    assert by_router[seeded["router1_id"]]["tx"] == 150
    assert by_router[seeded["router2_id"]]["rx"] == 600
    assert by_router[seeded["router2_id"]]["tx"] == 400


def test_dashboard_live_status_fetches_router_scope_once(client, monkeypatch):
    seeded = seed_router_data()
    seen_router_ids: list[int] = []

    class StubClient:
        def __init__(self, router_id: int):
            self.router_id = router_id

        def list_all_wireguard_peers(self):
            seen_router_ids.append(self.router_id)
            if self.router_id == seeded["router1_id"]:
                return [
                    SimpleNamespace(
                        interface="wg0",
                        public_key="pub-a",
                        disabled=False,
                        last_handshake=5,
                    )
                ]
            return [
                SimpleNamespace(
                    interface="wg0",
                    public_key="pub-b",
                    disabled=False,
                    last_handshake=999,
                )
            ]

    monkeypatch.setattr("backend.api.routes.make_client", lambda router: StubClient(router.id))

    response = client.get(
        f"/api/dashboard/live_status?router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert response.status_code == 200, response.text

    rows = sorted(response.json(), key=lambda row: row["peer_id"])
    assert seen_router_ids.count(seeded["router1_id"]) == 1
    assert seen_router_ids.count(seeded["router2_id"]) == 1
    assert rows == [
        {
            "peer_id": seeded["peer1_id"],
            "online": True,
            "raw_last_handshake": 5,
        },
        {
            "peer_id": seeded["peer2_id"],
            "online": False,
            "raw_last_handshake": 999,
        },
    ]


def test_scheduler_upserts_usage_minute_with_counter_resets(client, monkeypatch):
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
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="peer-a",
            public_key="pub-a",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()
        base_time = datetime(2026, 4, 3, 0, 0, 0, tzinfo=timezone.utc)
        db.add(UsageSample(peer_id=peer.id, ts=(base_time - timedelta(seconds=5)).replace(tzinfo=None), rx=100, tx=50, endpoint=""))
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    live_values = iter([(150, 80), (180, 100), (40, 20)])

    class FakeDateTime:
        current = base_time

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    class StubClient:
        def list_wireguard_peers(self, iface):
            rx, tx = next(live_values)
            return [
                SimpleNamespace(
                    public_key="pub-a",
                    ros_id="*1",
                    name="peer-a",
                    allowed_address="10.0.0.10/32",
                    disabled=False,
                    rx_bytes=rx,
                    tx_bytes=tx,
                    endpoint="",
                )
            ]

        def set_peer_disabled(self, iface, ros_id, disabled):
            return None

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    monkeypatch.setattr(scheduler_module, "datetime", FakeDateTime)

    for second in (0, 5, 10):
        FakeDateTime.current = base_time + timedelta(seconds=second)
        scheduler_module._poll_once()

    db = SessionLocal()
    try:
        rows = db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).all()
        assert len(rows) == 1
        assert rows[0].rx == 120
        assert rows[0].tx == 70
    finally:
        db.close()


def test_summary_endpoints_read_usage_minute(client):
    seeded = seed_minute_data()

    peers = client.get(f"/api/summary/peers?seconds=600&router_ids={seeded['router2_id']}")
    assert peers.status_code == 200, peers.text
    assert peers.json() == [
        {"peer_id": seeded["peer2_id"], "rx": 720, "tx": 300, "has_fair_usage": False, "fair_usage_throttled": False}
    ]

    raw = client.get(f"/api/summary/raw?seconds=600&router_ids={seeded['router2_id']}&interval=60")
    assert raw.status_code == 200, raw.text
    raw_rows = raw.json()
    assert len(raw_rows) == 2
    assert [row["rx"] for row in raw_rows] == [300, 420]
    assert [row["tx"] for row in raw_rows] == [100, 200]

    by_router = client.get(
        f"/api/summary/raw/by_router?seconds=600&interval=60&router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert by_router.status_code == 200, by_router.text
    rows = by_router.json()
    assert len(rows) == 4
    assert {row["router_id"] for row in rows} == {seeded["router1_id"], seeded["router2_id"]}

    peer_usage = client.get(f"/api/peers/{seeded['peer1_id']}/usage?window=raw&seconds=600&interval=60")
    assert peer_usage.status_code == 200, peer_usage.text
    points = peer_usage.json()
    assert len(points) == 2
    assert [row["rx"] for row in points] == [120, 180]
    assert [row["tx"] for row in points] == [60, 90]


def test_summary_endpoints_fall_back_when_minute_data_starts_after_cutoff(client):
    seeded = seed_router_data()

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
        db.add(
            UsageMinute(
                peer_id=seeded["peer2_id"],
                minute_ts=now - timedelta(minutes=1),
                rx=1,
                tx=2,
            )
        )
        db.commit()
    finally:
        db.close()

    peers = client.get(f"/api/summary/peers?seconds=600&router_ids={seeded['router2_id']}")
    assert peers.status_code == 200, peers.text
    assert peers.json() == [
        {"peer_id": seeded["peer2_id"], "rx": 600, "tx": 400, "has_fair_usage": False, "fair_usage_throttled": False}
    ]

    raw = client.get(f"/api/summary/raw?seconds=600&router_ids={seeded['router2_id']}&interval=60")
    assert raw.status_code == 200, raw.text
    assert len(raw.json()) == 1
    assert raw.json()[0]["rx"] == 600
    assert raw.json()[0]["tx"] == 400

    peer_usage = client.get(f"/api/peers/{seeded['peer2_id']}/usage?window=raw&seconds=600&interval=60")
    assert peer_usage.status_code == 200, peer_usage.text
    assert len(peer_usage.json()) == 1
    assert peer_usage.json()[0]["rx"] == 600
    assert peer_usage.json()[0]["tx"] == 400


def test_reset_and_purge_include_usage_minute(client):
    seeded = seed_minute_data()

    reset = client.post(f"/api/peers/{seeded['peer1_id']}/reset_metrics")
    assert reset.status_code == 200, reset.text
    assert reset.json()["deleted_minutes"] == 2

    purge = client.post("/api/admin/purge_usage")
    assert purge.status_code == 200, purge.text
    assert purge.json()["deleted_minutes"] == 2


def test_usage_maintenance_endpoints(client, monkeypatch):
    monkeypatch.setattr(
        "backend.api.routes.get_usage_maintenance_status",
        lambda: {
            "running": False,
            "phase": "idle",
            "backfilled_minutes": 0,
            "deleted_samples": 0,
            "deleted_minutes": 0,
            "deleted_daily": 0,
        },
    )
    response = client.get("/api/admin/usage_maintenance")
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "idle"

    monkeypatch.setattr(
        "backend.api.routes.start_usage_maintenance",
        lambda: (
            True,
            {
                "running": True,
                "phase": "queued",
                "backfilled_minutes": 0,
                "deleted_samples": 0,
                "deleted_minutes": 0,
                "deleted_daily": 0,
            },
        ),
    )
    started = client.post("/api/admin/usage_maintenance/run")
    assert started.status_code == 202, started.text
    assert started.json()["running"] is True
    assert started.json()["phase"] == "queued"
