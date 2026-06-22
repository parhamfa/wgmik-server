from datetime import datetime

from backend.db import SessionLocal
from backend.models import TelegramBroadcast, TelegramBroadcastRecipient, TelegramUser
from backend.telegram.outbox import acknowledge_recipient


def _add_tg_user(db, tg_id: int, username: str, *, blocked: bool = False) -> TelegramUser:
    user = TelegramUser(
        telegram_user_id=tg_id,
        telegram_username=username,
        first_name=username.title(),
        last_name="",
        language="en",
        is_blocked=blocked,
    )
    db.add(user)
    db.flush()
    return user


def test_create_broadcast_all_excludes_blocked_users(client, monkeypatch):
    dispatched: list[int] = []
    monkeypatch.setattr(
        "backend.telegram.outbox.dispatch_broadcast_async",
        lambda broadcast_id: dispatched.append(broadcast_id),
    )

    db = SessionLocal()
    try:
        active = _add_tg_user(db, 1001, "active")
        _add_tg_user(db, 1002, "blocked", blocked=True)
        active_id = active.id
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/telegram/broadcasts",
        data={"text": "Maintenance tonight", "recipient_mode": "all", "recipient_ids": "[]"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_count"] == 1
    assert body["status"] == "queued"
    assert dispatched == [body["id"]]

    detail = client.get(f"/api/telegram/broadcasts/{body['id']}")
    assert detail.status_code == 200, detail.text
    recipients = detail.json()["recipients"]
    assert len(recipients) == 1
    assert recipients[0]["telegram_user_id"] == active_id


def test_create_broadcast_selected_validates_recipient_choice(client, monkeypatch):
    monkeypatch.setattr("backend.telegram.outbox.dispatch_broadcast_async", lambda _broadcast_id: None)

    response = client.post(
        "/api/telegram/broadcasts",
        data={"text": "Hello", "recipient_mode": "selected", "recipient_ids": "[]"},
    )

    assert response.status_code == 400
    assert "Select at least one recipient" in response.text


def test_retry_failed_broadcast_requeues_failed_recipients(client, monkeypatch):
    dispatched: list[int] = []
    monkeypatch.setattr(
        "backend.telegram.outbox.dispatch_broadcast_async",
        lambda broadcast_id: dispatched.append(broadcast_id),
    )

    db = SessionLocal()
    try:
        user = _add_tg_user(db, 2001, "retry")
        broadcast = TelegramBroadcast(body="Retry me", status="failed", total_count=1, failed_count=1)
        db.add(broadcast)
        db.flush()
        db.add(
            TelegramBroadcastRecipient(
                broadcast_id=broadcast.id,
                telegram_user_id=user.id,
                chat_id=user.telegram_user_id,
                display_name="@retry",
                status="failed",
                error_code="TelegramForbiddenError",
                error_message="bot was blocked",
            )
        )
        db.commit()
        broadcast_id = broadcast.id
    finally:
        db.close()

    response = client.post(f"/api/telegram/broadcasts/{broadcast_id}/retry-failed")

    assert response.status_code == 200, response.text
    assert response.json()["queued"] == 1
    assert dispatched == [broadcast_id]

    db = SessionLocal()
    try:
        recipient = db.query(TelegramBroadcastRecipient).filter_by(broadcast_id=broadcast_id).one()
        broadcast = db.get(TelegramBroadcast, broadcast_id)
        assert recipient.status == "pending"
        assert recipient.error_message == ""
        assert broadcast.status == "queued"
        assert broadcast.failed_count == 0
    finally:
        db.close()


def test_acknowledgement_requires_matching_telegram_user(client):
    db = SessionLocal()
    try:
        user = _add_tg_user(db, 3001, "seen")
        broadcast = TelegramBroadcast(body="Ack me", status="sent", total_count=1, sent_count=1)
        db.add(broadcast)
        db.flush()
        recipient = TelegramBroadcastRecipient(
            broadcast_id=broadcast.id,
            telegram_user_id=user.id,
            chat_id=user.telegram_user_id,
            display_name="@seen",
            status="sent",
            telegram_message_id=77,
            sent_at=datetime.utcnow(),
        )
        db.add(recipient)
        db.commit()
        recipient_id = recipient.id
    finally:
        db.close()

    assert acknowledge_recipient(recipient_id, 9999) is False
    assert acknowledge_recipient(recipient_id, 3001) is True

    db = SessionLocal()
    try:
        recipient = db.get(TelegramBroadcastRecipient, recipient_id)
        broadcast = db.get(TelegramBroadcast, recipient.broadcast_id)
        assert recipient.status == "acknowledged"
        assert recipient.acknowledged_at is not None
        assert broadcast.acknowledged_count == 1
        assert broadcast.sent_count == 1
    finally:
        db.close()
