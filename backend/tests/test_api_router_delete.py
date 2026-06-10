import json
from datetime import datetime, timezone

import httpx

from backend.db import SessionLocal
from backend.destructive_ops import exclusive_operation_gate
from backend.models import (
    Action,
    FairUsageAssignment,
    FairUsageRule,
    FairUsageState,
    FairUsageTier,
    Peer,
    PeerTotalsMerge,
    Quota,
    Router,
    SettingsKV,
    TelegramPeerBinding,
    TelegramNotificationLog,
    TelegramSignupToken,
    TelegramUser,
    UsageDaily,
    UsageMinute,
    UsageMonthly,
    UsageSample,
)


def seed_router_delete_fixture():
    db = SessionLocal()
    try:
        delete_router = Router(
            name="Delete Me",
            host="10.0.0.2",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-delete",
            tls_verify=True,
            enabled=False,
        )
        keep_router = Router(
            name="Keep Me",
            host="10.0.0.3",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret-keep",
            tls_verify=True,
            enabled=True,
        )
        db.add_all([delete_router, keep_router])
        db.flush()

        delete_peer_1 = Peer(
            router_id=delete_router.id,
            interface="wg0",
            ros_id="*1",
            name="delete-peer-1",
            public_key="delete-pub-1",
            allowed_address="10.0.0.11/32",
            disabled=False,
            selected=True,
        )
        delete_peer_2 = Peer(
            router_id=delete_router.id,
            interface="wg0",
            ros_id="*2",
            name="delete-peer-2",
            public_key="delete-pub-2",
            allowed_address="10.0.0.12/32",
            disabled=True,
            selected=True,
        )
        keep_peer_1 = Peer(
            router_id=keep_router.id,
            interface="wg0",
            ros_id="*3",
            name="keep-peer-1",
            public_key="keep-pub-1",
            allowed_address="10.0.0.21/32",
            disabled=False,
            selected=True,
        )
        keep_peer_2 = Peer(
            router_id=keep_router.id,
            interface="wg0",
            ros_id="*4",
            name="keep-peer-2",
            public_key="keep-pub-2",
            allowed_address="10.0.0.22/32",
            disabled=False,
            selected=True,
        )
        db.add_all([delete_peer_1, delete_peer_2, keep_peer_1, keep_peer_2])
        db.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        db.add_all(
            [
                UsageSample(peer_id=delete_peer_1.id, ts=now, rx=10, tx=20),
                UsageMinute(peer_id=delete_peer_1.id, minute_ts=now, rx=30, tx=40),
                UsageDaily(peer_id=delete_peer_1.id, day="2026-04-22", rx=50, tx=60),
                UsageMonthly(peer_id=delete_peer_1.id, month_key="2026-04", rx=70, tx=80),
                UsageDaily(peer_id=keep_peer_1.id, day="2026-04-22", rx=500, tx=600),
            ]
        )

        db.add(Quota(peer_id=delete_peer_1.id, monthly_limit_bytes=1024, reset_day=1))
        db.add(Action(peer_id=delete_peer_1.id, ts=now, action="manual_disable", note="fixture"))

        tg_user = TelegramUser(
            telegram_user_id=12345,
            telegram_username="fixture_user",
            first_name="Fixture",
            last_name="User",
            language="en",
            is_blocked=False,
        )
        db.add(tg_user)
        db.flush()
        db.add(TelegramPeerBinding(telegram_user_id=tg_user.id, peer_id=delete_peer_1.id, visible=True))
        db.add(TelegramNotificationLog(telegram_user_id=tg_user.id, peer_id=delete_peer_1.id, event_type="quota_hit", message_hash="hash-1"))

        router_rule = FairUsageRule(
            name="Delete Router Rule",
            scope_type="router",
            router_id=delete_router.id,
            enabled=True,
        )
        db.add(router_rule)
        db.flush()
        db.add(
            FairUsageTier(
                rule_id=router_rule.id,
                sort_order=0,
                threshold_bytes=1024,
                name="tier-1",
                throttle_download_kbps=1000,
                throttle_upload_kbps=1000,
            )
        )
        db.add(FairUsageAssignment(rule_id=router_rule.id, peer_id=delete_peer_1.id))
        db.add(FairUsageState(peer_id=delete_peer_1.id, rule_id=router_rule.id, tier_id=None, throttled=True, ros_queue_id="q1"))

        db.add_all(
            [
                PeerTotalsMerge(
                    source_peer_id=delete_peer_1.id,
                    target_peer_id=keep_peer_1.id,
                    source_router_id=delete_router.id,
                    target_router_id=keep_router.id,
                    merge_mode="totals_only",
                    match_type="exact_public_key",
                    usage_minute_rows=1,
                    usage_daily_rows=1,
                    usage_monthly_rows=1,
                ),
                PeerTotalsMerge(
                    source_peer_id=keep_peer_2.id,
                    target_peer_id=delete_peer_2.id,
                    source_router_id=keep_router.id,
                    target_router_id=delete_router.id,
                    merge_mode="totals_only",
                    match_type="manual",
                    usage_minute_rows=0,
                    usage_daily_rows=0,
                    usage_monthly_rows=0,
                ),
            ]
        )

        db.add_all(
            [
                SettingsKV(key="active_router_id", value=str(delete_router.id)),
                SettingsKV(key="dashboard_selected_router_ids", value=json.dumps([delete_router.id, keep_router.id])),
                SettingsKV(key=f"peer_private_key:{delete_peer_1.id}", value="private"),
                SettingsKV(key=f"peer_preshared_key:{delete_peer_1.id}", value="psk"),
                SettingsKV(key=f"peer_export_config_name:{delete_peer_1.id}", value="cfg"),
                SettingsKV(key=f"peer_export_endpoint:{delete_peer_1.id}", value="ep"),
                SettingsKV(key=f"quota_valid_from:{delete_peer_1.id}", value="2026-04-01"),
                SettingsKV(key=f"quota_valid_until:{delete_peer_1.id}", value="2026-05-01"),
            ]
        )

        db.add_all(
            [
                TelegramSignupToken(token="tok-mixed", peer_ids=json.dumps([delete_peer_1.id, keep_peer_1.id]), single_use=True),
                TelegramSignupToken(token="tok-delete-only", peer_ids=json.dumps([delete_peer_2.id]), single_use=True),
            ]
        )

        db.commit()
        return {
            "delete_router_id": delete_router.id,
            "keep_router_id": keep_router.id,
            "delete_peer_1_id": delete_peer_1.id,
            "delete_peer_2_id": delete_peer_2.id,
            "keep_peer_1_id": keep_peer_1.id,
            "keep_peer_2_id": keep_peer_2.id,
        }
    finally:
        db.close()


def test_router_delete_preview_and_cleanup(client):
    seeded = seed_router_delete_fixture()

    preview = client.get(f"/api/routers/{seeded['delete_router_id']}/delete-impact")
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert preview_data["peer_count"] == 2
    assert preview_data["selected_peer_count"] == 2
    assert preview_data["usage_sample_rows"] == 1
    assert preview_data["usage_minute_rows"] == 1
    assert preview_data["usage_daily_rows"] == 1
    assert preview_data["usage_monthly_rows"] == 1
    assert preview_data["quota_count"] == 1
    assert preview_data["action_count"] == 1
    assert preview_data["telegram_binding_count"] == 1
    assert preview_data["telegram_log_count"] == 1
    assert preview_data["signup_token_count"] == 2
    assert preview_data["fair_usage_assignment_count"] == 1
    assert preview_data["fair_usage_state_count"] == 1
    assert preview_data["router_rule_count"] == 1
    assert preview_data["merge_ledger_count"] == 2
    assert preview_data["peer_setting_count"] == 6
    assert preview_data["dashboard_selected"] is True

    delete = client.delete(f"/api/routers/{seeded['delete_router_id']}")
    assert delete.status_code == 200, delete.text
    delete_data = delete.json()
    assert delete_data["router_id"] == seeded["delete_router_id"]
    assert delete_data["peer_count"] == 2
    assert delete_data["signup_tokens_updated"] == 1
    assert delete_data["signup_tokens_deleted"] == 1
    assert delete_data["backup_path"] is None
    assert delete_data["post_delete_quick_check"] == "ok"

    db = SessionLocal()
    try:
        assert db.get(Router, seeded["delete_router_id"]) is None
        assert db.get(Router, seeded["keep_router_id"]) is not None

        assert db.get(Peer, seeded["delete_peer_1_id"]) is None
        assert db.get(Peer, seeded["delete_peer_2_id"]) is None
        assert db.get(Peer, seeded["keep_peer_1_id"]) is not None
        assert db.get(Peer, seeded["keep_peer_2_id"]) is not None

        assert db.query(UsageSample).filter(UsageSample.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(UsageMinute).filter(UsageMinute.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(UsageDaily).filter(UsageDaily.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(UsageMonthly).filter(UsageMonthly.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(UsageDaily).filter(UsageDaily.peer_id == seeded["keep_peer_1_id"]).count() == 1

        assert db.query(Quota).filter(Quota.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(Action).filter(Action.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id.in_([seeded["delete_peer_1_id"], seeded["delete_peer_2_id"]])).count() == 0
        assert db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id.in_([seeded["delete_peer_1_id"], seeded["delete_peer_2_id"]])).count() == 0
        assert db.query(FairUsageRule).filter(FairUsageRule.router_id == seeded["delete_router_id"]).count() == 0
        assert db.query(FairUsageAssignment).filter(FairUsageAssignment.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(FairUsageState).filter(FairUsageState.peer_id == seeded["delete_peer_1_id"]).count() == 0
        assert db.query(FairUsageTier).count() == 0
        assert db.query(PeerTotalsMerge).count() == 0

        assert db.get(SettingsKV, "active_router_id") is None
        selected = db.get(SettingsKV, "dashboard_selected_router_ids")
        assert selected is not None
        assert json.loads(selected.value) == [seeded["keep_router_id"]]
        assert db.get(SettingsKV, f"peer_private_key:{seeded['delete_peer_1_id']}") is None
        assert db.get(SettingsKV, f"quota_valid_until:{seeded['delete_peer_1_id']}") is None

        mixed_token = db.query(TelegramSignupToken).filter_by(token="tok-mixed").first()
        assert mixed_token is not None
        assert json.loads(mixed_token.peer_ids) == [seeded["keep_peer_1_id"]]
        assert db.query(TelegramSignupToken).filter_by(token="tok-delete-only").first() is None
    finally:
        db.close()


def test_peer_delete_removes_peer_scoped_data_and_signup_refs(client, monkeypatch):
    calls = {"peer": [], "queue": []}

    class StubClient:
        def remove_wireguard_peer(self, interface, ros_id):
            calls["peer"].append((interface, ros_id))

        def remove_simple_queue(self, ros_id):
            calls["queue"].append(ros_id)

    import backend.api.routes as routes

    monkeypatch.setattr(routes, "make_client", lambda router: StubClient())

    db = SessionLocal()
    try:
        router = Router(
            name="Peer Delete Router",
            host="10.0.0.9",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*10",
            name="delete-me",
            public_key="delete-pub",
            allowed_address="10.0.0.10/32",
            disabled=False,
            selected=True,
        )
        other = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*11",
            name="keep-me",
            public_key="keep-pub",
            allowed_address="10.0.0.11/32",
            disabled=False,
            selected=True,
        )
        db.add_all([peer, other])
        db.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        db.add_all(
            [
                UsageSample(peer_id=peer.id, ts=now, rx=1, tx=2),
                UsageMinute(peer_id=peer.id, minute_ts=now, rx=3, tx=4),
                UsageDaily(peer_id=peer.id, day="2026-05-17", rx=5, tx=6),
                UsageMonthly(peer_id=peer.id, month_key="2026-05", rx=7, tx=8),
                Quota(peer_id=peer.id, monthly_limit_bytes=1024, reset_day=1),
                Action(peer_id=peer.id, ts=now, action="fixture", note="delete"),
            ]
        )
        rule = FairUsageRule(
            name="peer rule",
            quota_mode="combined",
            download_quota_bytes=1024,
            upload_quota_bytes=None,
            throttle_download_kbps=1000,
            throttle_upload_kbps=1000,
            time_scope="monthly",
            scope_type="peer",
            enabled=True,
        )
        db.add(rule)
        db.flush()
        db.add(FairUsageAssignment(rule_id=rule.id, peer_id=peer.id))
        db.add(FairUsageState(peer_id=peer.id, rule_id=rule.id, throttled=True, ros_queue_id="*queue"))
        tg_user = TelegramUser(
            telegram_user_id=999,
            telegram_username="peer_delete",
            first_name="Peer",
            last_name="Delete",
            language="en",
            is_blocked=False,
        )
        db.add(tg_user)
        db.flush()
        db.add(TelegramPeerBinding(telegram_user_id=tg_user.id, peer_id=peer.id, visible=True, created_at=now))
        db.add(
            TelegramNotificationLog(
                telegram_user_id=tg_user.id,
                peer_id=peer.id,
                event_type="quota_hit",
                sent_at=now,
                message_hash="hash",
            )
        )
        db.add(TelegramSignupToken(token="delete-only", peer_ids=json.dumps([peer.id]), created_by=1, single_use=False))
        db.add(TelegramSignupToken(token="mixed", peer_ids=json.dumps([peer.id, other.id]), created_by=1, single_use=False))
        db.add(PeerTotalsMerge(source_peer_id=peer.id, target_peer_id=other.id, source_router_id=router.id, target_router_id=router.id, merge_mode="totals_only", match_type="test"))
        db.add_all(
            [
                SettingsKV(key=f"peer_private_key:{peer.id}", value="private"),
                SettingsKV(key=f"peer_preshared_key:{peer.id}", value="psk"),
                SettingsKV(key=f"peer_export_config_name:{peer.id}", value="cfg"),
                SettingsKV(key=f"peer_export_endpoint:{peer.id}", value="ep"),
                SettingsKV(key=f"quota_valid_from:{peer.id}", value="2026-05-01"),
                SettingsKV(key=f"quota_valid_until:{peer.id}", value="2026-06-01"),
            ]
        )
        db.commit()
        peer_id = peer.id
        other_id = other.id
    finally:
        db.close()

    response = client.delete(f"/api/peers/{peer_id}")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_peer_id"] == peer_id
    assert calls["peer"] == [("wg0", "*10")]
    assert calls["queue"] == ["*queue"]

    db = SessionLocal()
    try:
        assert db.get(Peer, peer_id) is None
        assert db.get(Peer, other_id) is not None
        assert db.query(UsageSample).filter(UsageSample.peer_id == peer_id).count() == 0
        assert db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).count() == 0
        assert db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id).count() == 0
        assert db.query(UsageMonthly).filter(UsageMonthly.peer_id == peer_id).count() == 0
        assert db.query(Quota).filter(Quota.peer_id == peer_id).count() == 0
        assert db.query(Action).filter(Action.peer_id == peer_id).count() == 0
        assert db.query(FairUsageAssignment).filter(FairUsageAssignment.peer_id == peer_id).count() == 0
        assert db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id).count() == 0
        assert db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id == peer_id).count() == 0
        assert db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id == peer_id).count() == 0
        assert db.query(PeerTotalsMerge).filter(
            (PeerTotalsMerge.source_peer_id == peer_id) | (PeerTotalsMerge.target_peer_id == peer_id)
        ).count() == 0
        assert db.get(SettingsKV, f"peer_private_key:{peer_id}") is None
        assert db.get(SettingsKV, f"quota_valid_until:{peer_id}") is None
        assert db.query(TelegramSignupToken).filter_by(token="delete-only").first() is None
        mixed = db.query(TelegramSignupToken).filter_by(token="mixed").first()
        assert mixed is not None
        assert json.loads(mixed.peer_ids) == [other_id]
    finally:
        db.close()


def test_peer_delete_treats_router_404_as_already_gone(client, monkeypatch):
    calls = []

    class StubClient:
        def remove_wireguard_peer(self, interface, ros_id):
            calls.append((interface, ros_id))
            req = httpx.Request("DELETE", f"http://router/rest/interface/wireguard/peers/{ros_id}")
            res = httpx.Response(404, json={"message": "Not Found"}, request=req)
            raise httpx.HTTPStatusError("not found", request=req, response=res)

    import backend.api.routes as routes

    monkeypatch.setattr(routes, "make_client", lambda router: StubClient())

    db = SessionLocal()
    try:
        router = Router(
            name="Stale Peer Router",
            host="10.0.0.9",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*gone",
            name="stale",
            public_key="stale-pub",
            allowed_address="10.0.0.12/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()
        peer_id = peer.id
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/peers/{peer_id}")
    assert response.status_code == 200, response.text
    assert response.json()["router_deleted"] is False
    assert calls == [("wg0", "*gone")]

    db = SessionLocal()
    try:
        assert db.get(Peer, peer_id) is None
    finally:
        db.close()


def test_peer_delete_skips_router_for_hidden_peer(client, monkeypatch):
    calls = []

    class StubClient:
        def remove_wireguard_peer(self, interface, ros_id):
            calls.append((interface, ros_id))

    import backend.api.routes as routes

    monkeypatch.setattr(routes, "make_client", lambda router: StubClient())

    db = SessionLocal()
    try:
        router = Router(
            name="Hidden Peer Router",
            host="10.0.0.9",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*stale",
            name="hidden-stale",
            public_key="hidden-stale-pub",
            allowed_address="10.0.0.13/32",
            disabled=False,
            selected=False,
        )
        db.add(peer)
        db.flush()
        peer_id = peer.id
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/peers/{peer_id}")
    assert response.status_code == 200, response.text
    assert response.json()["router_deleted"] is False
    assert calls == []

    db = SessionLocal()
    try:
        assert db.get(Peer, peer_id) is None
    finally:
        db.close()


def test_peer_delete_keeps_selected_peer_on_router_500(client, monkeypatch):
    class StubClient:
        def remove_wireguard_peer(self, interface, ros_id):
            req = httpx.Request("DELETE", f"http://router/rest/interface/wireguard/peers/{ros_id}")
            res = httpx.Response(500, json={"message": "Internal Server Error"}, request=req)
            raise httpx.HTTPStatusError("router 500", request=req, response=res)

    import backend.api.routes as routes

    monkeypatch.setattr(routes, "make_client", lambda router: StubClient())

    db = SessionLocal()
    try:
        router = Router(
            name="Active Peer Router",
            host="10.0.0.9",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wg0",
            ros_id="*active",
            name="active-stale",
            public_key="active-stale-pub",
            allowed_address="10.0.0.14/32",
            disabled=False,
            selected=True,
        )
        db.add(peer)
        db.flush()
        peer_id = peer.id
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/peers/{peer_id}")
    assert response.status_code == 502, response.text

    db = SessionLocal()
    try:
        assert db.get(Peer, peer_id) is not None
    finally:
        db.close()


def test_router_delete_refuses_failed_quick_check(client, monkeypatch):
    seeded = seed_router_delete_fixture()

    from fastapi import HTTPException
    import backend.api.routes as routes

    def fail_quick_check(_db, phase):
        raise HTTPException(status_code=409, detail=f"database quick_check failed {phase}: broken")

    monkeypatch.setattr(routes, "_assert_sqlite_quick_check", fail_quick_check)

    response = client.delete(f"/api/routers/{seeded['delete_router_id']}")
    assert response.status_code == 409, response.text
    assert "quick_check failed before router delete" in response.json()["detail"]

    db = SessionLocal()
    try:
        assert db.get(Router, seeded["delete_router_id"]) is not None
        assert db.get(Peer, seeded["delete_peer_1_id"]) is not None
    finally:
        db.close()


def test_exclusive_delete_gate_blocks_other_api_requests(client):
    with exclusive_operation_gate.begin(
        key="router-delete",
        label="Deleting router Test",
        detail="Deleting router Test. Dashboard polling is temporarily paused; retry shortly.",
    ):
        blocked = client.get("/api/metrics")
        assert blocked.status_code == 503, blocked.text
        assert blocked.json()["operation"] == "router-delete"
        assert "retry" in blocked.json()["detail"].lower()
        assert blocked.headers["retry-after"] == "15"

        health = client.get("/health")
        assert health.status_code == 200, health.text
