"""Signup token generation and redemption logic."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    Peer,
    TelegramPeerBinding,
    TelegramSignupToken,
    TelegramUser,
)


def generate_token(
    db: Session,
    peer_ids: list[int],
    created_by: int,
    expires_at: Optional[datetime] = None,
    single_use: bool = True,
) -> TelegramSignupToken:
    token_str = secrets.token_urlsafe(32)
    tok = TelegramSignupToken(
        token=token_str,
        peer_ids=json.dumps(peer_ids),
        created_by=created_by,
        expires_at=expires_at,
        single_use=single_use,
    )
    db.add(tok)
    db.flush()
    return tok


def redeem_token(
    db: Session,
    token_str: str,
    tg_user_id: int,
    tg_username: str = "",
    first_name: str = "",
    last_name: str = "",
) -> tuple[Optional[TelegramUser], Optional[str]]:
    """Returns (tg_user, error_key). error_key is an i18n key or None on success."""
    tok = db.query(TelegramSignupToken).filter_by(token=token_str).first()
    if not tok:
        return None, "token_invalid"

    if tok.expires_at and tok.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None, "token_invalid"

    if tok.single_use and tok.used_by is not None:
        return None, "token_used"

    tg_user = db.query(TelegramUser).filter_by(telegram_user_id=tg_user_id).first()
    if not tg_user:
        tg_user = TelegramUser(
            telegram_user_id=tg_user_id,
            telegram_username=tg_username,
            first_name=first_name,
            last_name=last_name,
        )
        db.add(tg_user)
        db.flush()
    else:
        tg_user.telegram_username = tg_username or tg_user.telegram_username
        tg_user.first_name = first_name or tg_user.first_name
        tg_user.last_name = last_name or tg_user.last_name

    peer_ids = json.loads(tok.peer_ids or "[]")
    bound_count = 0
    for pid in peer_ids:
        peer = db.get(Peer, pid)
        if not peer:
            continue
        existing = db.query(TelegramPeerBinding).filter_by(
            telegram_user_id=tg_user.id, peer_id=pid
        ).first()
        if not existing:
            db.add(TelegramPeerBinding(telegram_user_id=tg_user.id, peer_id=pid))
            bound_count += 1

    tok.used_by = tg_user.id
    tok.used_at = datetime.now(timezone.utc)

    return tg_user, None
