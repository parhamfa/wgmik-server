"""Diagnosis helper for TG chart vs web panel (bindings + usage parity)."""

from datetime import datetime, timezone

from backend.db import SessionLocal
from backend.diagnose_tg_usage_charts import build_diagnosis_report
from backend.models import Peer, Router, TelegramPeerBinding, TelegramUser, UsageDaily


def _seed_asia_dup_iman():
    db = SessionLocal()
    router = Router(
        name="Asia",
        host="127.0.0.1",
        proto="rest",
        port=443,
        username="admin",
        secret_enc="x",
        tls_verify=False,
    )
    db.add(router)
    db.flush()
    old = Peer(
        router_id=router.id,
        interface="wg0",
        ros_id="*1",
        name="iman",
        public_key="pk_old_iman",
        allowed_address="10.0.0.2/32",
        selected=True,
    )
    new = Peer(
        router_id=router.id,
        interface="wg0",
        ros_id="*2",
        name="iman",
        public_key="pk_new_iman",
        allowed_address="10.0.0.3/32",
        selected=True,
    )
    db.add_all([old, new])
    db.flush()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.add(UsageDaily(peer_id=new.id, day=day, rx=5000, tx=7000))
    tu = TelegramUser(
        telegram_user_id=424242,
        telegram_username="tester",
        first_name="T",
        last_name="",
        language="en",
        is_blocked=False,
    )
    db.add(tu)
    db.flush()
    db.add(TelegramPeerBinding(telegram_user_id=tu.id, peer_id=old.id, visible=True))
    db.commit()
    old_id, new_id = int(old.id), int(new.id)
    db.close()
    return {"router": "Asia", "old_id": old_id, "new_id": new_id, "tg_uid": 424242}


def test_diagnosis_flags_duplicate_name_and_stale_binding(client):
    _seed_asia_dup_iman()
    db = SessionLocal()
    try:
        report = build_diagnosis_report(
            db,
            router_name_substr="asia",
            peer_name_filters=["iman"],
            telegram_telegram_user_id=424242,
        )
    finally:
        db.close()
    assert "DUPLICATE_NAME" in report
    assert "SUSPECT_STALE_BINDING" in report


def test_diagnosis_warns_unselected_peer(client):
    db = SessionLocal()
    router = Router(
        name="R-unsel",
        host="127.0.0.1",
        proto="rest",
        port=443,
        username="admin",
        secret_enc="x",
        tls_verify=False,
    )
    db.add(router)
    db.flush()
    p = Peer(
        router_id=router.id,
        interface="wg0",
        ros_id="*9",
        name="solo",
        public_key="pk_solo",
        allowed_address="10.0.0.9/32",
        selected=False,
    )
    db.add(p)
    db.flush()
    tu = TelegramUser(
        telegram_user_id=999001,
        telegram_username="solo_u",
        first_name="S",
        last_name="",
        language="en",
        is_blocked=False,
    )
    db.add(tu)
    db.flush()
    db.add(TelegramPeerBinding(telegram_user_id=tu.id, peer_id=p.id, visible=True))
    db.commit()
    db.close()

    db = SessionLocal()
    try:
        report = build_diagnosis_report(
            db,
            router_name_substr="unsel",
            peer_name_filters=["solo"],
            telegram_telegram_user_id=999001,
        )
    finally:
        db.close()
    assert "selected=False" in report
