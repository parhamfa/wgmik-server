from __future__ import annotations
from sqlalchemy import Column, Integer, String, Boolean, BigInteger, ForeignKey, UniqueConstraint, DateTime
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
