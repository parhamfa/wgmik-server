"""PATCH /api/peers/{id} name field: DB, RouterOS peer name, optional FU queue rename."""

from backend.db import SessionLocal
from backend.fair_usage_sync import FU_QUEUE_PREFIX
from backend.models import Action, FairUsageRule, FairUsageState, Peer, Router
from backend.api import routes as routes_module


def _seed_peer(*, name: str = "old", ros_id: str = "*1", with_fu: bool = False):
    db = SessionLocal()
    router = Router(
        name="R1",
        host="127.0.0.1",
        proto="rest",
        port=443,
        username="admin",
        secret_enc="x",
        tls_verify=False,
    )
    db.add(router)
    db.flush()
    peer = Peer(
        router_id=router.id,
        interface="wg0",
        ros_id=ros_id,
        name=name,
        public_key="pk1",
        allowed_address="10.0.0.2/32",
    )
    db.add(peer)
    db.flush()
    if with_fu:
        rule = FairUsageRule(
            name="G",
            quota_mode="combined",
            download_quota_bytes=1000,
            throttle_download_kbps=500,
            throttle_upload_kbps=500,
            time_scope="daily",
            scope_type="global",
        )
        db.add(rule)
        db.flush()
        db.add(
            FairUsageState(
                peer_id=peer.id,
                rule_id=rule.id,
                throttled=True,
                ros_queue_id="*9",
            )
        )
    db.commit()
    pid = peer.id
    db.close()
    return pid


def test_patch_peer_name_updates_router_and_db(client, monkeypatch):
    calls = {"peer_name": [], "queue_name": []}

    class Stub:
        def set_peer_name(self, interface: str, ros_id: str, name: str):
            calls["peer_name"].append((interface, ros_id, name))

        def set_simple_queue_name(self, ros_id: str, name: str):
            calls["queue_name"].append((ros_id, name))

    monkeypatch.setattr(routes_module, "make_client", lambda r: Stub())

    pid = _seed_peer(name="alpha", ros_id="*55")
    res = client.patch(f"/api/peers/{pid}", json={"name": "beta"})
    assert res.status_code == 200
    assert res.json()["name"] == "beta"

    db = SessionLocal()
    try:
        row = db.get(Peer, pid)
        assert row is not None
        assert row.name == "beta"
        actions = db.query(Action).filter(Action.peer_id == pid, Action.action == "peer_rename").all()
        assert len(actions) == 1
        assert "alpha" in (actions[0].note or "")
        assert "beta" in (actions[0].note or "")
    finally:
        db.close()

    assert calls["peer_name"] == [("wg0", "*55", "beta")]
    assert calls["queue_name"] == []


def test_patch_peer_name_renames_fu_queue_when_throttled(client, monkeypatch):
    calls = {"queue_name": []}

    class Stub:
        def set_peer_name(self, interface: str, ros_id: str, name: str):
            pass

        def set_simple_queue_name(self, ros_id: str, name: str):
            calls["queue_name"].append((ros_id, name))

    monkeypatch.setattr(routes_module, "make_client", lambda r: Stub())

    pid = _seed_peer(name="raya", ros_id="*1", with_fu=True)
    res = client.patch(f"/api/peers/{pid}", json={"name": "raya2"})
    assert res.status_code == 200
    want = f"{FU_QUEUE_PREFIX}raya2"
    assert calls["queue_name"] == [("*9", want)]


def test_patch_peer_name_without_ros_id_db_only(client, monkeypatch):
    invocations = []

    def fail_make(_r):
        invocations.append(1)
        raise AssertionError("make_client should not be used when ros_id is empty")

    monkeypatch.setattr(routes_module, "make_client", fail_make)

    pid = _seed_peer(name="local", ros_id="")
    res = client.patch(f"/api/peers/{pid}", json={"name": "renamed-local"})
    assert res.status_code == 200
    assert res.json()["name"] == "renamed-local"
    assert invocations == []

    db = SessionLocal()
    try:
        assert db.get(Peer, pid).name == "renamed-local"
    finally:
        db.close()


def test_patch_peer_selected_logs_manual_show_hide(client):
    pid = _seed_peer(name="hidden", ros_id="")

    res = client.patch(f"/api/peers/{pid}", json={"selected": False})
    assert res.status_code == 200
    assert res.json()["selected"] is False

    res = client.patch(f"/api/peers/{pid}", json={"selected": True})
    assert res.status_code == 200
    assert res.json()["selected"] is True

    db = SessionLocal()
    try:
        row = db.get(Peer, pid)
        assert row is not None
        assert row.selected is True
        actions = db.query(Action).filter(Action.peer_id == pid).order_by(Action.id.asc()).all()
        assert [a.action for a in actions] == ["manual_hide", "manual_show"]
    finally:
        db.close()
