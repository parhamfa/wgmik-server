import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import backend.api.routes as routes_module
import backend.scheduler as scheduler_module
from backend.db import SessionLocal
from backend.models import Action, Peer, Router, SettingsKV, UsageDaily, UsageMinute, UsageSample
from backend.routeros.client_base import WGInterfaceConfig
from backend.security import SecretBox
from backend.settings import settings


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
            ros_version="7.15",
            ros_supported=True,
        )
        router2 = Router(
            name="Router B",
            host="10.0.0.2",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-b",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
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


def seed_peer_for_key_renewal(old_private_key=None):
    db = SessionLocal()
    try:
        router = Router(
            name="Router Renew",
            host="10.0.0.9",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-renew",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.flush()

        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*9",
            name="peer-renew",
            public_key="pub-old",
            allowed_address="10.0.0.99/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()

        db.add(
            UsageSample(
                peer_id=peer.id,
                ts=datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) - timedelta(minutes=1),
                rx=100,
                tx=50,
                endpoint="",
            )
        )
        if old_private_key:
            db.add(
                SettingsKV(
                    key=f"peer_private_key:{peer.id}",
                    value=SecretBox(settings.secret_key).encrypt(old_private_key),
                )
            )
        db.commit()
        return {"router_id": router.id, "peer_id": peer.id}
    finally:
        db.close()


def test_create_router_peer_stores_export_prefs_and_passes_router_keys(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Router Add",
            host="10.0.0.10",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-add",
            tls_verify=True,
        )
        db.add(router)
        db.commit()
        router_id = router.id
    finally:
        db.close()

    private_key = base64.b64encode(b"a" * 32).decode("utf-8")
    preshared_key = base64.b64encode(b"b" * 32).decode("utf-8")
    public_key = base64.b64encode(b"c" * 32).decode("utf-8")
    calls = {}

    class StubClient:
        def list_wireguard_peers(self, iface):
            calls["list_iface"] = iface
            return []

        def add_wireguard_peer(
            self,
            interface,
            public_key,
            allowed_address,
            name="",
            disabled=False,
            private_key=None,
            preshared_key=None,
            client_endpoint=None,
        ):
            calls["add"] = {
                "interface": interface,
                "public_key": public_key,
                "allowed_address": allowed_address,
                "name": name,
                "disabled": disabled,
                "private_key": private_key,
                "preshared_key": preshared_key,
                "client_endpoint": client_endpoint,
            }
            return "*77"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

    response = client.post(
        f"/api/routers/{router_id}/peers/add",
        json={
            "interface": "wg0",
            "name": "alice",
            "public_key": public_key,
            "allowed_address": "10.65.74.100/32",
            "private_key": private_key,
            "preshared_key": preshared_key,
            "config_name": "alice-phone",
            "custom_endpoint": "vpn.example.com:1443",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "alice"
    assert body["public_key"] == public_key
    assert calls["list_iface"] == "wg0"
    assert calls["add"] == {
        "interface": "wg0",
        "public_key": public_key,
        "allowed_address": "10.65.74.100/32",
        "name": "alice",
        "disabled": False,
        "private_key": private_key,
        "preshared_key": preshared_key,
        "client_endpoint": "vpn.example.com:1443",
    }

    db = SessionLocal()
    try:
        peer_id = body["id"]
        assert SecretBox(settings.secret_key).decrypt(db.get(SettingsKV, f"peer_private_key:{peer_id}").value) == private_key
        assert SecretBox(settings.secret_key).decrypt(db.get(SettingsKV, f"peer_preshared_key:{peer_id}").value) == preshared_key
        assert db.get(SettingsKV, f"peer_export_config_name:{peer_id}").value == "alice-phone"
        assert db.get(SettingsKV, f"peer_export_endpoint:{peer_id}").value == "vpn.example.com:1443"
    finally:
        db.close()


def test_router_interface_detail_includes_interface_addresses(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Router Interface",
            host="10.0.0.10",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-interface",
            tls_verify=True,
        )
        db.add(router)
        db.commit()
        router_id = router.id
    finally:
        db.close()

    class StubClient:
        def get_wireguard_interface(self, interface):
            assert interface == "wg0"
            return WGInterfaceConfig(
                name="wg0",
                public_key="server-public",
                listen_port=51820,
                addresses=["10.65.74.1/24"],
            )

        def get_primary_ipv4(self):
            return "203.0.113.10"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

    response = client.get(f"/api/routers/{router_id}/interfaces/wg0")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "name": "wg0",
        "public_key": "server-public",
        "listen_port": 51820,
        "public_host": "203.0.113.10",
        "addresses": ["10.65.74.1/24"],
    }


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


def test_settings_legacy_active_scope_sanitized_to_all(client):
    db = SessionLocal()
    try:
        db.add(SettingsKV(key="dashboard_router_scope", value="active"))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["dashboard_router_scope"] == "all"


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

    monkeypatch.setattr("backend.api.routes.make_client", lambda router, **_: StubClient(router.id))

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


def test_dashboard_live_status_returns_reachable_router_when_other_fails(client, monkeypatch):
    """Unreachable routers must not prevent live status for routers that respond."""
    seeded = seed_router_data()

    class StubClient:
        def __init__(self, router_id: int):
            self.router_id = router_id

        def list_all_wireguard_peers(self):
            if self.router_id == seeded["router2_id"]:
                raise OSError("unreachable")
            return [
                SimpleNamespace(
                    interface="wg0",
                    public_key="pub-a",
                    disabled=False,
                    last_handshake=12,
                )
            ]

    monkeypatch.setattr("backend.api.routes.make_client", lambda router, **_: StubClient(router.id))

    response = client.get(
        f"/api/dashboard/live_status?router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows == [
        {
            "peer_id": seeded["peer1_id"],
            "online": True,
            "raw_last_handshake": 12,
        }
    ]


def test_dashboard_live_status_skips_disabled_router(client, monkeypatch):
    """Paused (enabled=False) routers must not be polled and must not appear in live status."""
    seeded = seed_router_data()
    db = SessionLocal()
    try:
        r2 = db.get(Router, seeded["router2_id"])
        r2.enabled = False
        db.commit()
    finally:
        db.close()

    seen_router_ids: list[int] = []

    class StubClient:
        def __init__(self, router_id: int):
            self.router_id = router_id

        def list_all_wireguard_peers(self):
            seen_router_ids.append(self.router_id)
            return [
                SimpleNamespace(
                    interface="wg0",
                    public_key="pub-a",
                    disabled=False,
                    last_handshake=4,
                )
            ]

    monkeypatch.setattr("backend.api.routes.make_client", lambda router, **_: StubClient(router.id))

    response = client.get(
        f"/api/dashboard/live_status?router_ids={seeded['router1_id']}&router_ids={seeded['router2_id']}"
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert seeded["router2_id"] not in seen_router_ids
    assert rows == [
        {
            "peer_id": seeded["peer1_id"],
            "online": True,
            "raw_last_handshake": 4,
        }
    ]


def test_router_create_and_update_enabled_field(client, monkeypatch):
    class StubClient:
        def get_system_version(self):
            return "7.15"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

    create_resp = client.post(
        "/api/routers",
        json={
            "name": "Toggle",
            "host": "10.0.0.99",
            "proto": "rest",
            "port": 443,
            "username": "admin",
            "password": "pw",
            "tls_verify": True,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    body = create_resp.json()
    assert body["enabled"] is True
    router_id = body["id"]

    pause = client.put(f"/api/routers/{router_id}", json={"enabled": False})
    assert pause.status_code == 200, pause.text
    assert pause.json()["enabled"] is False

    listing = client.get("/api/routers").json()
    matched = next(r for r in listing if r["id"] == router_id)
    assert matched["enabled"] is False

    resume = client.put(f"/api/routers/{router_id}", json={"enabled": True})
    assert resume.status_code == 200, resume.text
    assert resume.json()["enabled"] is True


def test_router_create_rejects_unsupported_routeros(client, monkeypatch):
    class StubClient:
        def get_system_version(self):
            return "7.14.3"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

    response = client.post(
        "/api/routers",
        json={
            "name": "Old ROS",
            "host": "10.0.0.14",
            "proto": "rest",
            "port": 443,
            "username": "admin",
            "password": "pw",
            "tls_verify": True,
        },
    )
    assert response.status_code == 400, response.text
    assert "RouterOS 7.15+ is required" in response.json()["detail"]


def test_scheduler_skips_disabled_router(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Paused",
            host="10.0.0.50",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-paused",
            tls_verify=True,
            enabled=False,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="peer-paused",
            public_key="pub-paused",
            allowed_address="10.0.0.50/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    called: list[int] = []

    class StubClient:
        def list_wireguard_peers(self, iface):
            called.append(1)
            return []

        def set_peer_disabled(self, iface, ros_id, disabled):
            return None

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    scheduler_module._poll_once()
    assert called == [], "scheduler must not connect to a paused router"

    db = SessionLocal()
    try:
        samples = db.query(UsageSample).filter(UsageSample.peer_id == peer_id).all()
        assert samples == []
    finally:
        db.close()


def test_scheduler_marks_selected_peer_missing_without_hiding(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Drift Router",
            host="10.0.0.70",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
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
            name="missing-peer",
            public_key="pub-missing",
            allowed_address="10.0.0.70/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    class StubClient:
        def list_all_wireguard_peers(self):
            return []

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        peer = db.get(Peer, peer_id)
        assert peer is not None
        assert peer.selected is True
        assert peer.router_sync_status == "missing"
        assert db.query(Action).filter(Action.peer_id == peer_id, Action.action == "router_missing").count() == 1
    finally:
        db.close()


def test_scheduler_creates_and_removes_pending_new_peer(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="New Peer Router",
            host="10.0.0.71",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.commit()
        router_id = router.id
    finally:
        db.close()

    live_rows = [
        SimpleNamespace(
            interface="wg0",
            public_key="pub-new",
            ros_id="*7",
            name="new-peer",
            allowed_address="10.0.0.71/32",
            disabled=False,
            rx_bytes=0,
            tx_bytes=0,
            endpoint="",
            client_endpoint="",
            last_handshake=None,
        )
    ]

    class StubClient:
        def list_all_wireguard_peers(self):
            return list(live_rows)

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        peer = db.query(Peer).filter(Peer.router_id == router_id, Peer.public_key == "pub-new").one()
        assert peer.selected is False
        assert peer.router_sync_status == "new"
        peer_id = peer.id
    finally:
        db.close()

    live_rows.clear()
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        assert db.get(Peer, peer_id) is None
    finally:
        db.close()


def test_scheduler_keeps_pending_new_peer_flagged_while_present(client, monkeypatch):
    """A RouterOS-discovered peer must stay 'new'/unselected across polls until an admin
    accepts or hides it; it must not silently auto-resolve to 'synced' and drop into the
    hidden list while still present on the router."""
    db = SessionLocal()
    try:
        router = Router(
            name="Sticky New Peer Router",
            host="10.0.0.74",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.commit()
        router_id = router.id
    finally:
        db.close()

    live_rows = [
        SimpleNamespace(
            interface="wg0",
            public_key="pub-sticky-new",
            ros_id="*9",
            name="sticky-new",
            allowed_address="10.0.0.74/32",
            disabled=False,
            rx_bytes=0,
            tx_bytes=0,
            endpoint="",
            client_endpoint="",
            last_handshake=None,
        )
    ]

    class StubClient:
        def list_all_wireguard_peers(self):
            return list(live_rows)

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    monkeypatch.setattr(scheduler_module, "_enforce_fair_usage", lambda db, peer, client, now_utc: None)

    scheduler_module._poll_once()
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        peer = db.query(Peer).filter(Peer.router_id == router_id, Peer.public_key == "pub-sticky-new").one()
        assert peer.selected is False
        assert peer.router_sync_status == "new"
    finally:
        db.close()


def test_scheduler_clears_missing_when_peer_reappears(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Reappear Router",
            host="10.0.0.72",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
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
            name="old-name",
            public_key="pub-return",
            allowed_address="10.0.0.72/32",
            disabled=False,
            selected=True,
            router_sync_status="missing",
        )
        db.add(peer)
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    class StubClient:
        def list_all_wireguard_peers(self):
            return [
                SimpleNamespace(
                    interface="wg0",
                    public_key="pub-return",
                    ros_id="*2",
                    name="returned",
                    allowed_address="10.0.0.73/32",
                    disabled=False,
                    rx_bytes=10,
                    tx_bytes=5,
                    endpoint="",
                    client_endpoint="",
                    last_handshake=None,
                )
            ]

    monkeypatch.setattr("backend.scheduler.make_client", lambda router: StubClient())
    monkeypatch.setattr(scheduler_module, "_enforce_fair_usage", lambda db, peer, client, now_utc: None)
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        peer = db.get(Peer, peer_id)
        assert peer is not None
        assert peer.router_sync_status == "synced"
        assert peer.ros_id == "*2"
        assert peer.name == "returned"
        assert db.query(UsageSample).filter(UsageSample.peer_id == peer_id).count() == 1
    finally:
        db.close()


def test_resolve_missing_peer_hide_and_delete_are_local(client, monkeypatch):
    db = SessionLocal()
    try:
        router = Router(
            name="Resolve Missing",
            host="10.0.0.74",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.flush()
        hide_peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="hide",
            public_key="pub-hide-missing",
            allowed_address="10.0.0.74/32",
            disabled=False,
            selected=True,
            router_sync_status="missing",
        )
        delete_peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*2",
            name="delete",
            public_key="pub-delete-missing",
            allowed_address="10.0.0.75/32",
            disabled=False,
            selected=True,
            router_sync_status="missing",
        )
        db.add_all([hide_peer, delete_peer])
        db.commit()
        hide_id = hide_peer.id
        delete_id = delete_peer.id
    finally:
        db.close()

    def fail_make_client(router):
        raise AssertionError("resolve must not touch RouterOS")

    monkeypatch.setattr(routes_module, "make_client", fail_make_client)

    hide_resp = client.post(f"/api/peers/{hide_id}/router-sync/resolve", json={"action": "hide"})
    assert hide_resp.status_code == 200, hide_resp.text
    assert hide_resp.json()["selected"] is False
    assert hide_resp.json()["router_sync_status"] == "synced"

    delete_resp = client.post(f"/api/peers/{delete_id}/router-sync/resolve", json={"action": "delete"})
    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json()["router_deleted"] is False

    db = SessionLocal()
    try:
        assert db.get(Peer, delete_id) is None
    finally:
        db.close()


def test_resolve_new_peer_accept_or_hide(client):
    db = SessionLocal()
    try:
        router = Router(
            name="Resolve New",
            host="10.0.0.76",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
            ros_version="7.15",
            ros_supported=True,
        )
        db.add(router)
        db.flush()
        accept_peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*1",
            name="accept",
            public_key="pub-accept-new",
            allowed_address="10.0.0.76/32",
            disabled=False,
            selected=False,
            router_sync_status="new",
        )
        hide_peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*2",
            name="hide",
            public_key="pub-hide-new",
            allowed_address="10.0.0.77/32",
            disabled=False,
            selected=False,
            router_sync_status="new",
        )
        db.add_all([accept_peer, hide_peer])
        db.commit()
        accept_id = accept_peer.id
        hide_id = hide_peer.id
    finally:
        db.close()

    accept_resp = client.post(f"/api/peers/{accept_id}/router-sync/resolve", json={"action": "accept"})
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["selected"] is True
    assert accept_resp.json()["router_sync_status"] == "synced"

    hide_resp = client.post(f"/api/peers/{hide_id}/router-sync/resolve", json={"action": "hide"})
    assert hide_resp.status_code == 200, hide_resp.text
    assert hide_resp.json()["selected"] is False
    assert hide_resp.json()["router_sync_status"] == "synced"


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
            ros_version="7.15",
            ros_supported=True,
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
        def list_all_wireguard_peers(self):
            rx, tx = next(live_values)
            return [
                SimpleNamespace(
                    interface="wg0",
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
        assert rows[0].rx == 80
        assert rows[0].tx == 50
    finally:
        db.close()


def test_scheduler_quarantines_near_32bit_drop_direction_for_local_day(client, monkeypatch):
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
            name="peer-a",
            public_key="pub-a",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()
        base_time = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
        db.add(
            UsageSample(
                peer_id=peer.id,
                ts=(base_time - timedelta(seconds=5)).replace(tzinfo=None),
                rx=100,
                tx=4_200_000_000,
                endpoint="",
            )
        )
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    live_values = iter([(150, 12_000_000), (200, 20_000_000)])

    class FakeDateTime:
        current = base_time

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    class StubClient:
        def list_all_wireguard_peers(self):
            rx, tx = next(live_values)
            return [
                SimpleNamespace(
                    interface="wg0",
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

    for second in (0, 5):
        FakeDateTime.current = base_time + timedelta(seconds=second)
        scheduler_module._poll_once()

    db = SessionLocal()
    try:
        rows = db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).all()
        assert len(rows) == 1
        assert rows[0].rx == 100
        assert rows[0].tx == 0
        assert db.query(UsageSample).filter(UsageSample.peer_id == peer_id).count() == 3
        anomaly = (
            db.query(Action)
            .filter(Action.peer_id == peer_id, Action.action == "usage_anomaly")
            .all()
        )
        assert len(anomaly) == 1
        assert "Quarantined tx usage" in anomaly[0].note
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


def test_raw_fallbacks_quarantine_near_32bit_wireguard_spikes(client):
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
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        db.add_all(
            [
                UsageSample(peer_id=peer.id, ts=(now - timedelta(minutes=3)).replace(tzinfo=None), rx=100, tx=4_200_000_000, endpoint=""),
                UsageSample(peer_id=peer.id, ts=(now - timedelta(minutes=2)).replace(tzinfo=None), rx=150, tx=12_000_000, endpoint=""),
                UsageSample(peer_id=peer.id, ts=(now - timedelta(minutes=1)).replace(tzinfo=None), rx=200, tx=1_500_000_000, endpoint=""),
            ]
        )
        db.commit()
        peer_id = peer.id
    finally:
        db.close()

    peers = client.get("/api/summary/peers?seconds=600")
    assert peers.status_code == 200, peers.text
    peer_row = next(row for row in peers.json() if row["peer_id"] == peer_id)
    assert peer_row["rx"] == 100
    assert peer_row["tx"] == 0

    raw = client.get("/api/summary/raw?seconds=600&interval=60")
    assert raw.status_code == 200, raw.text
    assert sum(row["rx"] for row in raw.json()) == 100
    assert sum(row["tx"] for row in raw.json()) == 0

    peer_usage = client.get(f"/api/peers/{peer_id}/usage?window=raw&seconds=600&interval=60")
    assert peer_usage.status_code == 200, peer_usage.text
    assert sum(row["rx"] for row in peer_usage.json()) == 100
    assert sum(row["tx"] for row in peer_usage.json()) == 0


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


def test_renew_peer_keys_updates_router_db_and_scheduler_match(client, monkeypatch):
    old_private = base64.b64encode(b"o" * 32).decode("utf-8")
    new_private = base64.b64encode(b"n" * 32).decode("utf-8")
    new_public = "pub-renewed"
    seeded = seed_peer_for_key_renewal(old_private)
    calls: dict[str, tuple[str, str, str, str]] = {}

    class StubClient:
        def set_peer_keys(self, iface, ros_id, public_key, private_key):
            calls["set_peer_keys"] = (iface, ros_id, public_key, private_key)

        def list_wireguard_peers(self, iface):
            return [
                SimpleNamespace(
                    interface=iface,
                    public_key=new_public,
                    ros_id="*9",
                    name="peer-renew",
                    allowed_address="10.0.0.99/32",
                    disabled=False,
                    rx_bytes=150,
                    tx_bytes=90,
                    endpoint="198.51.100.10:51820",
                )
            ]

        def list_all_wireguard_peers(self):
            return self.list_wireguard_peers("wg0")

    stub_client = StubClient()
    monkeypatch.setattr(routes_module, "_generate_wg_keypair_b64", lambda: (new_private, new_public))
    monkeypatch.setattr(routes_module, "make_client", lambda router: stub_client)

    response = client.post(f"/api/peers/{seeded['peer_id']}/renew_keys")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["private_key"] == new_private
    assert body["peer"]["id"] == seeded["peer_id"]
    assert body["peer"]["public_key"] == new_public
    assert calls["set_peer_keys"] == ("wg0", "*9", new_public, new_private)

    private_response = client.get(f"/api/peers/{seeded['peer_id']}/client_private_key")
    assert private_response.status_code == 200, private_response.text
    assert private_response.json() == {"private_key": new_private}

    db = SessionLocal()
    try:
        peer = db.get(Peer, seeded["peer_id"])
        assert peer is not None
        assert peer.id == seeded["peer_id"]
        assert peer.public_key == new_public
        assert peer.selected is True
        renew_actions = db.query(Action).filter(Action.peer_id == peer.id, Action.action == "renew_keys").all()
        assert len(renew_actions) == 1
    finally:
        db.close()

    monkeypatch.setattr(scheduler_module, "make_client", lambda router: stub_client)
    monkeypatch.setattr(scheduler_module, "_enforce_fair_usage", lambda db, peer, client, now_utc: None)
    scheduler_module._poll_once()

    db = SessionLocal()
    try:
        peer = db.get(Peer, seeded["peer_id"])
        assert peer is not None
        assert peer.selected is True
        assert db.query(Action).filter(Action.peer_id == peer.id, Action.action == "router_missing").count() == 0
        assert db.query(UsageSample).filter(UsageSample.peer_id == peer.id).count() == 2
    finally:
        db.close()


def test_renew_peer_keys_failure_keeps_existing_public_and_private_key(client, monkeypatch):
    old_private = base64.b64encode(b"p" * 32).decode("utf-8")
    new_private = base64.b64encode(b"q" * 32).decode("utf-8")
    seeded = seed_peer_for_key_renewal(old_private)

    class FailingClient:
        def set_peer_keys(self, iface, ros_id, public_key, private_key):
            raise RuntimeError("router rejected key update")

    monkeypatch.setattr(routes_module, "_generate_wg_keypair_b64", lambda: (new_private, "pub-should-not-stick"))
    monkeypatch.setattr(routes_module, "make_client", lambda router: FailingClient())

    response = client.post(f"/api/peers/{seeded['peer_id']}/renew_keys")
    assert response.status_code == 502, response.text
    assert "router key renewal failed" in response.json()["detail"]

    private_response = client.get(f"/api/peers/{seeded['peer_id']}/client_private_key")
    assert private_response.status_code == 200, private_response.text
    assert private_response.json() == {"private_key": old_private}

    db = SessionLocal()
    try:
        peer = db.get(Peer, seeded["peer_id"])
        assert peer is not None
        assert peer.public_key == "pub-old"
        assert db.query(Action).filter(Action.peer_id == peer.id, Action.action == "renew_keys").count() == 0
        kv = db.get(SettingsKV, f"peer_private_key:{peer.id}")
        assert kv is not None
        assert SecretBox(settings.secret_key).decrypt(kv.value) == old_private
    finally:
        db.close()
