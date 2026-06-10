from __future__ import annotations
from typing import Optional
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
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    ros_version: Mapped[str] = mapped_column(String(64), default="", server_default="")
    ros_version_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ros_supported: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

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
    router_sync_status: Mapped[str] = mapped_column(String(16), default="synced", server_default="synced")
    router_sync_first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    router_sync_last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

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


class PeerTotalsMerge(Base):
    __tablename__ = "peer_totals_merge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"), unique=True, index=True)
    target_peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="CASCADE"), index=True)
    source_router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    target_router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    merge_mode: Mapped[str] = mapped_column(String(32), default="totals_only")
    match_type: Mapped[str] = mapped_column(String(64), default="")
    usage_minute_rows: Mapped[int] = mapped_column(Integer, default=0)
    usage_daily_rows: Mapped[int] = mapped_column(Integer, default=0)
    usage_monthly_rows: Mapped[int] = mapped_column(Integer, default=0)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    #: If True and this rule matches, later rules may still run and override its throttle.
    passthrough: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: When True, enforcement uses :class:`FairUsageTier` rows (combined usage only); thresholds are a ladder.
    tiered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assignments: Mapped[list["FairUsageAssignment"]] = relationship("FairUsageAssignment", back_populates="rule", cascade="all, delete-orphan")
    tiers: Mapped[list["FairUsageTier"]] = relationship(
        "FairUsageTier", back_populates="rule", cascade="all, delete-orphan", order_by="FairUsageTier.sort_order"
    )


class FairUsageTier(Base):
    """Step within a tiered fair-usage rule: at/above ``threshold_bytes`` combined usage, apply this tier's throttle."""

    __tablename__ = "fair_usage_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("fair_usage_rules.id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    threshold_bytes: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(128), default="")
    throttle_download_kbps: Mapped[int] = mapped_column(Integer)
    throttle_upload_kbps: Mapped[int] = mapped_column(Integer)

    rule: Mapped["FairUsageRule"] = relationship("FairUsageRule", back_populates="tiers")


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
    tier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fair_usage_tiers.id", ondelete="SET NULL"), nullable=True)
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSecurityEvent(Base):
    __tablename__ = "user_security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(String, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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


class TelegramUserNotificationPreference(Base):
    __tablename__ = "telegram_user_notification_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("telegram_user_id", "event_type", name="uq_tg_user_notification_pref"),
    )


class TelegramNotificationLog(Base):
    __tablename__ = "telegram_notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id", ondelete="CASCADE"))
    peer_id: Mapped[int] = mapped_column(ForeignKey("peers.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    message_hash: Mapped[str] = mapped_column(String(64), default="")
