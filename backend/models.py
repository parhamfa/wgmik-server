from __future__ import annotations
from sqlalchemy import Column, Integer, String, Boolean, BigInteger, ForeignKey, UniqueConstraint, DateTime, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime
from .db import Base


class Router(Base):
    __tablename__ = "routers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    proto: Mapped[str] = mapped_column(String(10), default="rest")  # rest | api
    port: Mapped[int] = mapped_column(Integer, default=443)
    username: Mapped[str] = mapped_column(String(255))
    secret_enc: Mapped[str] = mapped_column(String)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True)

    peers: Mapped[list[Peer]] = relationship("Peer", back_populates="router", cascade="all, delete-orphan")


class Peer(Base):
    __tablename__ = "peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    interface: Mapped[str] = mapped_column(String(128))
    ros_id: Mapped[str] = mapped_column(String(64), default="")  # RouterOS internal .id
    name: Mapped[str] = mapped_column(String(255), default="")
    public_key: Mapped[str] = mapped_column(String(255))
    allowed_address: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str] = mapped_column(String(255), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)

    router: Mapped[Router] = relationship("Router", back_populates="peers")

    __table_args__ = (
        UniqueConstraint("router_id", "interface", "public_key", name="uq_peer_router_iface_pubkey"),
    )


class UsageSample(Base):
    __tablename__ = "usage_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    rx: Mapped[int] = mapped_column(BigInteger)
    tx: Mapped[int] = mapped_column(BigInteger)
    endpoint: Mapped[str] = mapped_column(String(255), default="")


class UsageDaily(Base):
    __tablename__ = "usage_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    rx: Mapped[int] = mapped_column(BigInteger, default=0)
    tx: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("peer_id", "day", name="uq_daily_peer_day"),
    )


class UsageMinute(Base):
    __tablename__ = "usage_minute"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    minute_ts: Mapped[datetime] = mapped_column(DateTime)
    rx: Mapped[int] = mapped_column(BigInteger, default=0)
    tx: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("peer_id", "minute_ts", name="uq_usage_minute_peer_ts"),
        Index("ix_usage_minute_minute_ts", "minute_ts"),
    )


class UsageMonthly(Base):
    __tablename__ = "usage_monthly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    month_key: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    rx: Mapped[int] = mapped_column(BigInteger, default=0)
    tx: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("peer_id", "month_key", name="uq_month_peer"),
    )


class Quota(Base):
    __tablename__ = "quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"), unique=True)
    monthly_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reset_day: Mapped[int] = mapped_column(Integer, default=1)


class FairUsageRule(Base):
    __tablename__ = "fair_usage_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(512), default="")
    quota_mode: Mapped[str] = mapped_column(String(16), default="combined")  # combined | independent
    download_quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    upload_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=True)
    throttle_download_kbps: Mapped[int] = mapped_column(Integer, default=1000)
    throttle_upload_kbps: Mapped[int] = mapped_column(Integer, default=1000)
    time_scope: Mapped[str] = mapped_column(String(16), default="monthly")  # legacy; sync from scope_period_*
    scope_period_count: Mapped[int] = mapped_column(Integer, default=1)
    scope_period_unit: Mapped[str] = mapped_column(String(8), default="month")  # hour | day | week | month
    scope_type: Mapped[str] = mapped_column(String(16), default="global")  # global | router | peer
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assignments: Mapped[list["FairUsageAssignment"]] = relationship("FairUsageAssignment", back_populates="rule", cascade="all, delete-orphan")


class FairUsageAssignment(Base):
    __tablename__ = "fair_usage_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("fair_usage_rules.id", ondelete="CASCADE"))
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))

    rule: Mapped["FairUsageRule"] = relationship("FairUsageRule", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("rule_id", "peer_id", name="uq_fu_rule_peer"),
    )


class FairUsageState(Base):
    __tablename__ = "fair_usage_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"), unique=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("fair_usage_rules.id", ondelete="CASCADE"))
    throttled: Mapped[bool] = mapped_column(Boolean, default=False)
    ros_queue_id: Mapped[str] = mapped_column(String(64), nullable=True, default="")
    throttled_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="SET NULL"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    action: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String)


class SettingsKV(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String)

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Telegram bot tables ──────────────────────────────────────────────

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str] = mapped_column(String(255), default="")
    first_name: Mapped[str] = mapped_column(String(255), default="")
    last_name: Mapped[str] = mapped_column(String(255), default="")
    language: Mapped[str] = mapped_column(String(4), default="en")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bindings: Mapped[list["TelegramPeerBinding"]] = relationship(
        "TelegramPeerBinding", back_populates="tg_user", cascade="all, delete-orphan"
    )


class TelegramPeerBinding(Base):
    __tablename__ = "telegram_peer_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"))
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"))
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tg_user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="bindings")

    __table_args__ = (
        UniqueConstraint("telegram_user_id", "peer_id", name="uq_tg_user_peer"),
    )


class TelegramSignupToken(Base):
    __tablename__ = "telegram_signup_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    peer_ids: Mapped[str] = mapped_column(String, default="[]")  # JSON array
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_by: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    single_use: Mapped[bool] = mapped_column(Boolean, default=True)


class TelegramNotificationConfig(Base):
    __tablename__ = "telegram_notification_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), unique=True)
    notify_clients: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TelegramNotificationLog(Base):
    __tablename__ = "telegram_notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"))
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    message_hash: Mapped[str] = mapped_column(String(64), default="")
