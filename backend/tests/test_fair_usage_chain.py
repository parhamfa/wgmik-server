from datetime import datetime

from backend.db import SessionLocal
from backend.fair_usage_peer_status_dto import build_fair_usage_peer_status_dto
from backend.fair_usage_sync import evaluate_fair_usage_chain
from backend.models import FairUsageRule, Peer, Router, UsageMinute


def _seed_peer_and_usage():
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
        ros_id="*1",
        name="Peer 1",
        public_key="pk1",
        allowed_address="10.0.0.2/32",
    )
    db.add(peer)
    db.flush()
    db.add(
        UsageMinute(
            peer_id=peer.id,
            minute_ts=datetime.utcnow().replace(second=0, microsecond=0),
            rx=5000,
            tx=0,
        )
    )
    db.commit()
    return db, peer


def test_passthrough_allows_later_rule_to_override(client):
    db, peer = _seed_peer_and_usage()
    try:
        db.add_all(
            [
                FairUsageRule(
                    name="Soft",
                    quota_mode="combined",
                    download_quota_bytes=3000,
                    throttle_download_kbps=500,
                    throttle_upload_kbps=500,
                    scope_period_count=1,
                    scope_period_unit="day",
                    time_scope="daily",
                    scope_type="global",
                    sort_order=0,
                    passthrough=True,
                    enabled=True,
                ),
                FairUsageRule(
                    name="Hard",
                    quota_mode="combined",
                    download_quota_bytes=4000,
                    throttle_download_kbps=300,
                    throttle_upload_kbps=300,
                    scope_period_count=1,
                    scope_period_unit="day",
                    time_scope="daily",
                    scope_type="global",
                    sort_order=1,
                    passthrough=False,
                    enabled=True,
                ),
            ]
        )
        db.commit()

        winner = evaluate_fair_usage_chain(db, peer)
        assert winner is not None
        assert winner[0].name == "Hard"

        dto = build_fair_usage_peer_status_dto(db, peer)
        effective = next(r for r in dto.rules if r.is_effective)
        assert effective.rule_name == "Hard"
        assert dto.rule_name == "Hard"
        assert dto.throttle_download_kbps == 300
    finally:
        db.close()


def test_non_passthrough_rule_stops_chain(client):
    db, peer = _seed_peer_and_usage()
    try:
        db.add_all(
            [
                FairUsageRule(
                    name="Soft",
                    quota_mode="combined",
                    download_quota_bytes=3000,
                    throttle_download_kbps=500,
                    throttle_upload_kbps=500,
                    scope_period_count=1,
                    scope_period_unit="day",
                    time_scope="daily",
                    scope_type="global",
                    sort_order=0,
                    passthrough=False,
                    enabled=True,
                ),
                FairUsageRule(
                    name="Hard",
                    quota_mode="combined",
                    download_quota_bytes=4000,
                    throttle_download_kbps=300,
                    throttle_upload_kbps=300,
                    scope_period_count=1,
                    scope_period_unit="day",
                    time_scope="daily",
                    scope_type="global",
                    sort_order=1,
                    passthrough=False,
                    enabled=True,
                ),
            ]
        )
        db.commit()

        winner = evaluate_fair_usage_chain(db, peer)
        assert winner is not None
        assert winner[0].name == "Soft"

        dto = build_fair_usage_peer_status_dto(db, peer)
        effective = next(r for r in dto.rules if r.is_effective)
        assert effective.rule_name == "Soft"
        assert dto.rule_name == "Soft"
        assert dto.throttle_download_kbps == 500
    finally:
        db.close()
