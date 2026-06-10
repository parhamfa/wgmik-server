from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.models import FairUsageState, FairUsageTier, Peer, Router


def seed_fair_usage_peers():
    db = SessionLocal()
    try:
        router = Router(
            name="Router A",
            host="10.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
            tls_verify=True,
        )
        db.add(router)
        db.flush()
        peers = [
            Peer(
                router_id=router.id,
                interface="wg0",
                ros_id=f"*{idx}",
                name=name,
                public_key=f"pub-{idx}",
                allowed_address=f"10.65.74.{idx}/32",
                disabled=False,
                selected=True,
            )
            for idx, name in enumerate(["max", "sadra", "mac"], start=1)
        ]
        db.add_all(peers)
        db.flush()
        ids = {"router_id": router.id, "peer_ids": [p.id for p in peers]}
        db.commit()
        return ids
    finally:
        db.close()


def fair_usage_payload(peer_ids: list[int]):
    return {
        "name": "no-limit",
        "description": "",
        "quota_mode": "combined",
        "download_quota_bytes": 100 * 1024 * 1024 * 1024,
        "upload_quota_bytes": None,
        "throttle_download_kbps": 8000,
        "throttle_upload_kbps": 8000,
        "scope_period_count": 1,
        "scope_period_unit": "day",
        "scope_type": "peer",
        "router_id": None,
        "peer_ids": peer_ids,
        "sort_order": 0,
        "passthrough": False,
        "enabled": True,
        "tiered": False,
        "tiers": [],
    }


def assigned_peer_ids(rule: dict) -> list[int]:
    return sorted(p["peer_id"] for p in rule["assigned_peers"])


def tier_rows(*values: float) -> list[dict]:
    return [
        {
            "threshold_bytes": round(value * 1024 * 1024 * 1024),
            "name": f"tier-{idx}",
            "throttle_download_kbps": max(100, 8000 // idx),
            "throttle_upload_kbps": max(100, 3000 // idx),
            "sort_order": idx - 1,
        }
        for idx, value in enumerate(values, start=1)
    ]


def test_update_fair_usage_rule_replaces_peer_assignments(client: TestClient):
    seeded = seed_fair_usage_peers()
    first, second, third = seeded["peer_ids"]

    created = client.post("/api/fair-usage/rules", json=fair_usage_payload([first, second]))
    assert created.status_code == 200, created.text
    rule_id = created.json()["id"]

    updated = client.put(f"/api/fair-usage/rules/{rule_id}", json=fair_usage_payload([second, third]))
    assert updated.status_code == 200, updated.text
    assert assigned_peer_ids(updated.json()) == sorted([second, third])

    listed = client.get("/api/fair-usage/rules")
    assert listed.status_code == 200, listed.text
    assert assigned_peer_ids(listed.json()[0]) == sorted([second, third])


def test_partial_fair_usage_rule_update_preserves_peer_assignments(client: TestClient):
    seeded = seed_fair_usage_peers()
    first, second, _ = seeded["peer_ids"]

    created = client.post("/api/fair-usage/rules", json=fair_usage_payload([first, second]))
    assert created.status_code == 200, created.text
    rule_id = created.json()["id"]

    updated = client.put(f"/api/fair-usage/rules/{rule_id}", json={"sort_order": 4})
    assert updated.status_code == 200, updated.text
    assert updated.json()["sort_order"] == 4
    assert assigned_peer_ids(updated.json()) == sorted([first, second])


def test_update_tiered_rule_clears_state_tier_reference_before_replacing_tiers(client: TestClient):
    seeded = seed_fair_usage_peers()
    first = seeded["peer_ids"][0]
    payload = fair_usage_payload([first])
    payload.update(
        {
            "tiered": True,
            "tiers": tier_rows(1.5, 3, 9),
        }
    )
    created = client.post("/api/fair-usage/rules", json=payload)
    assert created.status_code == 200, created.text
    rule_id = created.json()["id"]

    db = SessionLocal()
    try:
        tier = db.query(FairUsageTier).filter(FairUsageTier.rule_id == rule_id).first()
        assert tier is not None
        db.add(FairUsageState(peer_id=first, rule_id=rule_id, tier_id=tier.id, throttled=True, ros_queue_id="q1"))
        db.commit()
    finally:
        db.close()

    updated = client.put(f"/api/fair-usage/rules/{rule_id}", json={"tiered": True, "tiers": tier_rows(1.5, 1.75, 3, 4, 9)})
    assert updated.status_code == 200, updated.text
    assert [tier["threshold_bytes"] for tier in updated.json()["tiers"]] == sorted(
        tier["threshold_bytes"] for tier in updated.json()["tiers"]
    )

    db = SessionLocal()
    try:
        state = db.query(FairUsageState).filter(FairUsageState.rule_id == rule_id, FairUsageState.peer_id == first).one()
        assert state.tier_id is None
    finally:
        db.close()
