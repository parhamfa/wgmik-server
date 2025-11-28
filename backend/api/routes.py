from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..db import get_db, Base, engine, SessionLocal
from ..settings import settings
from ..scheduler import update_scheduler_interval
from ..security import SecretBox
from ..models import Router, SettingsKV, Peer, UsageDaily, UsageMonthly, UsageSample, Quota
from ..routeros.factory import make_client
from datetime import datetime, timezone
from typing import Optional, List
from datetime import timedelta
from ipaddress import ip_network
import os

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


router = APIRouter(prefix="/api", tags=["api"])


class SettingsDTO(BaseModel):
    poll_interval_seconds: int
    online_threshold_seconds: int
    monthly_reset_day: int
    timezone: str
    show_kind_pills: bool = True
    show_hw_stats: bool = True
    dashboard_refresh_seconds: int = 30
    peer_default_scope_unit: str = "days"
    peer_default_scope_value: int = 14


class MetricsDTO(BaseModel):
    cpu_percent: Optional[float] = None
    load_1: Optional[float] = None
    load_5: Optional[float] = None
    load_15: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None


@router.on_event("startup")
def _init_db():
    Base.metadata.create_all(bind=engine)
    # Load persisted settings from DB into in-memory settings object
    db = SessionLocal()
    try:
        for key in ("poll_interval_seconds", "online_threshold_seconds", "monthly_reset_day", "timezone"):
            kv = db.get(SettingsKV, key)
            if not kv:
                continue
            if key in ("poll_interval_seconds", "online_threshold_seconds", "monthly_reset_day"):
                try:
                    setattr(settings, key, int(kv.value))
                except ValueError:
                    continue
            elif key == "timezone":
                settings.timezone = kv.value
    finally:
        db.close()


@router.get("/metrics", response_model=MetricsDTO)
def get_metrics():
    """Lightweight system metrics for dashboard display."""
    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    load_1: Optional[float] = None
    load_5: Optional[float] = None
    load_15: Optional[float] = None

    if psutil:
        try:
            cpu_percent = float(psutil.cpu_percent(interval=0.0))
            vm = psutil.virtual_memory()
            mem_percent = float(vm.percent)
            mem_used = int(vm.used)
            mem_total = int(vm.total)
        except Exception:
            pass

    # Fallback to load average if available
    try:
        l1, l5, l15 = os.getloadavg()
        load_1, load_5, load_15 = float(l1), float(l5), float(l15)
    except (OSError, AttributeError):
        pass

    return MetricsDTO(
        cpu_percent=cpu_percent,
        load_1=load_1,
        load_5=load_5,
        load_15=load_15,
        mem_percent=mem_percent,
        mem_used=mem_used,
        mem_total=mem_total,
    )


@router.get("/settings", response_model=SettingsDTO)
def get_settings(db: Session = Depends(get_db)):
    # Base values from in-memory settings (already hydrated from DB on startup)
    data: dict = {
        "poll_interval_seconds": settings.poll_interval_seconds,
        "online_threshold_seconds": settings.online_threshold_seconds,
        "monthly_reset_day": settings.monthly_reset_day,
        "timezone": settings.timezone,
        "show_kind_pills": True,
        "show_hw_stats": True,
        "dashboard_refresh_seconds": 30,
        "peer_default_scope_unit": "days",
        "peer_default_scope_value": 14,
    }
    # Overlay any values persisted in SettingsKV
    for key in (
        "show_kind_pills",
        "show_hw_stats",
        "dashboard_refresh_seconds",
        "peer_default_scope_unit",
        "peer_default_scope_value",
    ):
        kv = db.get(SettingsKV, key)
        if not kv:
            continue
        if key in ("dashboard_refresh_seconds", "peer_default_scope_value"):
            try:
                data[key] = int(kv.value)
            except ValueError:
                continue
        elif key in ("show_kind_pills", "show_hw_stats"):
            data[key] = kv.value.lower() in ("1", "true", "yes", "on")
        elif key == "peer_default_scope_unit":
            if kv.value in ("minutes", "hours", "days"):
                data[key] = kv.value
    return SettingsDTO(**data)


@router.put("/settings", response_model=SettingsDTO)
def put_settings(dto: SettingsDTO, db: Session = Depends(get_db)):
    # Persist to SettingsKV for runtime updates
    for key, value in dto.model_dump().items():
        kv = db.get(SettingsKV, key)
        if kv is None:
            kv = SettingsKV(key=key, value=str(value))
            db.add(kv)
        else:
            kv.value = str(value)
    db.commit()
    # Reflect into in-memory settings
    settings.poll_interval_seconds = dto.poll_interval_seconds
    settings.online_threshold_seconds = dto.online_threshold_seconds
    settings.monthly_reset_day = dto.monthly_reset_day
    settings.timezone = dto.timezone
    # Hot-reload scheduler interval
    try:
        update_scheduler_interval(dto.poll_interval_seconds)
    except Exception:
        pass
    return dto


class RouterCreateDTO(BaseModel):
    name: str
    host: str
    proto: str  # rest | rest-http | api | api-plain
    port: int
    username: str
    password: str
    tls_verify: bool = True


class RouterUpdateDTO(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    proto: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls_verify: Optional[bool] = None


class RouterDTO(BaseModel):
    id: int
    name: str
    host: str
    proto: str
    port: int
    username: str
    tls_verify: bool

    class Config:
        from_attributes = True
class WGInterfaceDTO(BaseModel):
    name: str
    public_key: str
    listen_port: int
    public_host: str



@router.post("/routers", response_model=RouterDTO)
def create_router(dto: RouterCreateDTO, db: Session = Depends(get_db)):
    if dto.proto not in ("rest", "api", "rest-http", "api-plain"):
        raise HTTPException(status_code=400, detail="proto must be one of 'rest', 'rest-http', 'api', 'api-plain'")
    box = SecretBox(settings.secret_key)
    r = Router(
        name=dto.name,
        host=dto.host,
        proto=dto.proto,
        port=dto.port,
        username=dto.username,
        secret_enc=box.encrypt(dto.password),
        tls_verify=dto.tls_verify,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.get("/routers/{router_id}", response_model=RouterDTO)
def get_router(router_id: int, db: Session = Depends(get_db)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    return row


@router.put("/routers/{router_id}", response_model=RouterDTO)
def update_router(router_id: int, dto: RouterUpdateDTO, db: Session = Depends(get_db)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    if dto.proto is not None:
        if dto.proto not in ("rest", "rest-http", "api", "api-plain"):
            raise HTTPException(status_code=400, detail="invalid proto")
        row.proto = dto.proto
    if dto.name is not None:
        row.name = dto.name
    if dto.host is not None:
        row.host = dto.host
    if dto.port is not None:
        row.port = dto.port
    if dto.username is not None:
        row.username = dto.username
    if dto.password:
        box = SecretBox(settings.secret_key)
        row.secret_enc = box.encrypt(dto.password)
    if dto.tls_verify is not None:
        row.tls_verify = dto.tls_verify
    db.commit()
    db.refresh(row)
    return row


@router.delete("/routers/{router_id}")
def delete_router(router_id: int, db: Session = Depends(get_db)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/routers", response_model=list[RouterDTO])
def list_routers(db: Session = Depends(get_db)):
    return db.query(Router).all()


@router.get("/routers/{router_id}/interfaces", response_model=List[str])
def list_interfaces(router_id: int, db: Session = Depends(get_db)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        return client.list_wireguard_interfaces()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


@router.get("/routers/{router_id}/interfaces/{iface}", response_model=WGInterfaceDTO)
def get_interface(router_id: int, iface: str, db: Session = Depends(get_db)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        cfg = client.get_wireguard_interface(iface)
        primary_host = ""
        try:
            primary_host = client.get_primary_ipv4()
        except Exception:
            primary_host = ""
        host = primary_host or router.host
        return WGInterfaceDTO(name=cfg.name, public_key=cfg.public_key, listen_port=cfg.listen_port, public_host=host)
    except KeyError:
        raise HTTPException(status_code=404, detail="interface not found")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


class PeerDTO(BaseModel):
    id: Optional[int] = None
    interface: str
    name: str
    public_key: str
    allowed_address: str
    disabled: bool
    endpoint: str
    last_handshake: Optional[int] = None
    online: bool


@router.get("/routers/{router_id}/peers", response_model=List[PeerDTO])
def list_peers(router_id: int, interface: str, db: Session = Depends(get_db)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        rows = client.list_wireguard_peers(interface)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")
    out: List[PeerDTO] = []
    for p in rows:
        online = False
        if p.last_handshake:
            # p.last_handshake is age in seconds since last handshake
            online = p.last_handshake <= settings.online_threshold_seconds
        # check if exists
        existing = db.query(Peer).filter(
            Peer.router_id == router_id,
            Peer.interface == interface,
            Peer.public_key == p.public_key,
        ).first()
        out.append(PeerDTO(
            id=existing.id if existing else None,
            interface=p.interface,
            name=p.name,
            public_key=p.public_key,
            allowed_address=p.allowed_address,
            disabled=p.disabled,
            endpoint=p.endpoint,
            last_handshake=p.last_handshake,
            online=online,
        ))
    return out


class PeerImportItem(BaseModel):
    interface: str
    public_key: str
    selected: bool = True


@router.post("/routers/{router_id}/peers/import")
def import_peers(router_id: int, items: list[PeerImportItem], db: Session = Depends(get_db)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    # Build a map from public_key to live peer to pull fields
    interfaces = set(i.interface for i in items)
    live: dict[tuple[str, str], object] = {}
    for iface in interfaces:
        for p in client.list_wireguard_peers(iface):
            live[(iface, p.public_key)] = p
    imported = 0
    for it in items:
        key = (it.interface, it.public_key)
        row = live.get(key)
        if not row:
            continue
        exists = db.query(Peer).filter(
            Peer.router_id == router_id,
            Peer.interface == it.interface,
            Peer.public_key == it.public_key,
        ).first()
        if exists:
            exists.selected = it.selected
            continue
        db.add(Peer(
            router_id=router_id,
            interface=row.interface,
            ros_id=row.ros_id,
            name=row.name or "",
            public_key=row.public_key,
            allowed_address=row.allowed_address,
            comment="",
            disabled=row.disabled,
            selected=it.selected,
        ))
        imported += 1
    db.commit()
    return {"imported": imported}


@router.post("/routers/{router_id}/test")
def test_router_connection(router_id: int, db: Session = Depends(get_db)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        _ = client.list_wireguard_interfaces()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


# Demo/seed and DB-driven endpoints for frontend development
@router.post("/demo/seed")
def seed_demo(db: Session = Depends(get_db)):
    # Create demo router
    r = db.query(Router).filter(Router.name == "demo").first()
    if not r:
        r = Router(name="demo", host="demo", proto="rest", port=443, username="demo", secret_enc="", tls_verify=False)
        db.add(r)
        db.commit()
        db.refresh(r)
    # Create demo peers
    base_ip = "10.65.74."
    peers: List[Peer] = []
    for i in range(2, 8):
        pub = f"demo-pubkey-{i}"
        p = db.query(Peer).filter(Peer.router_id == r.id, Peer.public_key == pub).first()
        if not p:
            p = Peer(
                router_id=r.id,
                interface="wgmik",
                ros_id=f"*{i}",
                name=f"peer-{i}",
                public_key=pub,
                allowed_address=f"{base_ip}{i}/32",
                comment="",
                disabled=False,
                selected=True,
            )
            db.add(p)
            db.flush()
        peers.append(p)
    # Add a couple of outbound peers as dummies (0.0.0.0/0 and ::/0)
    outbound_specs = [
        ("demo-pubkey-out4", "outbound-v4", "0.0.0.0/0"),
        ("demo-pubkey-out6", "outbound-v6", "::/0"),
    ]
    idx = 100
    for pub, name, addr in outbound_specs:
        p = db.query(Peer).filter(Peer.router_id == r.id, Peer.public_key == pub).first()
        if not p:
            p = Peer(
                router_id=r.id,
                interface="wgmik",
                ros_id=f"*{idx}",
                name=name,
                public_key=pub,
                allowed_address=addr,
                comment="",
                disabled=False,
                selected=False,
            )
            db.add(p)
            db.flush()
        peers.append(p)
        idx += 1
    db.commit()

    # Seed usage daily for last 14 days
    today = datetime.utcnow().date()
    for p in peers:
        for d in range(14):
            day = (today - timedelta(days=d)).strftime("%Y-%m-%d")
            row = db.query(UsageDaily).filter(UsageDaily.peer_id == p.id, UsageDaily.day == day).first()
            if not row:
                row = UsageDaily(peer_id=p.id, day=day, rx=100_000_000 + d * 1_000_000, tx=50_000_000 + d * 500_000)
                db.add(row)
    db.commit()
    return {"ok": True, "router_id": r.id, "peer_count": len(peers)}


class PeerListDTO(BaseModel):
    id: int
    router_id: int
    interface: str
    name: str
    public_key: str
    allowed_address: str
    disabled: bool
    selected: bool

    class Config:
        from_attributes = True


class PeerCreateDemoDTO(BaseModel):
    interface: str
    name: str
    public_key: str
    allowed_address: str
    comment: Optional[str] = ""


@router.post("/demo/peers", response_model=PeerListDTO)
def create_demo_peer(dto: PeerCreateDemoDTO, db: Session = Depends(get_db)):
    # Inbound only guard
    if dto.allowed_address.strip() in ("0.0.0.0/0", "::/0"):
        raise HTTPException(status_code=400, detail="only inbound peers are allowed in demo (address must not be 0.0.0.0/0 or ::/0)")
    # CIDR sanity (IPv4 or IPv6)
    try:
        _ = ip_network(dto.allowed_address.strip(), strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid allowed_address format")
    # Ensure demo router exists
    r = db.query(Router).filter(Router.name == "demo").first()
    if not r:
        r = Router(name="demo", host="demo", proto="rest", port=443, username="demo", secret_enc="", tls_verify=False)
        db.add(r)
        db.commit()
        db.refresh(r)
    # Uniqueness check
    exists = db.query(Peer).filter(
        Peer.router_id == r.id,
        Peer.interface == dto.interface,
        Peer.public_key == dto.public_key,
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="peer with same public_key already exists on this interface")
    row = Peer(
        router_id=r.id,
        interface=dto.interface,
        ros_id="",  # demo
        name=dto.name or "",
        public_key=dto.public_key,
        allowed_address=dto.allowed_address,
        comment=dto.comment or "",
        disabled=False,
        selected=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/peers", response_model=List[PeerListDTO])
def list_saved_peers(router_id: Optional[int] = None, interface: Optional[str] = None, selected_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(Peer)
    if router_id is not None:
        q = q.filter(Peer.router_id == router_id)
    if interface is not None:
        q = q.filter(Peer.interface == interface)
    if selected_only:
        q = q.filter(Peer.selected == True)
    return q.order_by(Peer.id.asc()).all()


class PeerUpdateDTO(BaseModel):
    selected: Optional[bool] = None
    disabled: Optional[bool] = None


@router.patch("/peers/{peer_id}", response_model=PeerListDTO)
def update_peer(peer_id: int, dto: PeerUpdateDTO, db: Session = Depends(get_db)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    if dto.selected is not None:
        row.selected = dto.selected
    if dto.disabled is not None:
        row.disabled = dto.disabled
        # Best-effort push to RouterOS; do not fail demo if router is unreachable
        try:
            r = db.get(Router, row.router_id)
            if r and row.ros_id:
                client = make_client(r)
                client.set_peer_disabled(row.interface, row.ros_id, bool(dto.disabled))
        except Exception:
            pass
    db.commit()
    db.refresh(row)
    return row


@router.delete("/peers/{peer_id}")
def delete_peer(peer_id: int, db: Session = Depends(get_db)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_peer_id": peer_id}


class UsagePointDTO(BaseModel):
    day: str
    rx: int
    tx: int


@router.get("/peers/{peer_id}/usage", response_model=List[UsagePointDTO])
def get_peer_usage(
    peer_id: int,
    window: str = "daily",
    seconds: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    window=daily: aggregate per day from UsageDaily (existing behaviour).
    window=raw: last `seconds` worth of UsageSample deltas, labelled by time of day.
    """
    if window == "daily":
        rows = (
            db.query(UsageDaily)
            .filter(UsageDaily.peer_id == peer_id)
            .order_by(UsageDaily.day.asc())
            .all()
        )
        return [UsagePointDTO(day=r.day, rx=r.rx, tx=r.tx) for r in rows]

    if window == "raw":
        # Default to last hour if not specified
        lookback = seconds if seconds and seconds > 0 else 3600
        cutoff = datetime.utcnow() - timedelta(seconds=lookback)
        samples = (
            db.query(UsageSample)
            .filter(UsageSample.peer_id == peer_id, UsageSample.ts >= cutoff)
            .order_by(UsageSample.ts.asc())
            .all()
        )
        points: List[UsagePointDTO] = []
        prev: Optional[UsageSample] = None
        for s in samples:
            if prev is None:
                prev = s
                continue
            # Compute deltas, guard against counter resets
            drx = s.rx - prev.rx if s.rx >= prev.rx else 0
            dtx = s.tx - prev.tx if s.tx >= prev.tx else 0
            prev = s
            if drx <= 0 and dtx <= 0:
                continue
            label = s.ts.strftime("%H:%M:%S")
            points.append(UsagePointDTO(day=label, rx=drx, tx=dtx))
        return points

    raise HTTPException(status_code=400, detail="window must be 'daily' or 'raw'")


@router.post("/peers/{peer_id}/reset_metrics")
def reset_peer_metrics(peer_id: int, db: Session = Depends(get_db)):
    # Validate peer exists
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    deleted_samples = db.query(UsageSample).filter(UsageSample.peer_id == peer_id).delete()
    deleted_daily = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id).delete()
    deleted_monthly = db.query(UsageMonthly).filter(UsageMonthly.peer_id == peer_id).delete()
    db.commit()
    return {
        "ok": True,
        "deleted_samples": deleted_samples,
        "deleted_daily": deleted_daily,
        "deleted_monthly": deleted_monthly,
    }


# Quota endpoints (demo-friendly; time-based fields stored in SettingsKV)
class QuotaDTO(BaseModel):
    monthly_limit_bytes: Optional[int] = None
    reset_day: int
    valid_from: Optional[str] = None  # ISO8601
    valid_until: Optional[str] = None  # ISO8601
    used_rx: int
    used_tx: int


@router.get("/peers/{peer_id}/quota", response_model=QuotaDTO)
def get_peer_quota(peer_id: int, db: Session = Depends(get_db)):
    q = db.query(Quota).filter(Quota.peer_id == peer_id).first()
    monthly_limit_bytes = q.monthly_limit_bytes if q and q.monthly_limit_bytes else None
    reset_day_val = q.reset_day if q else settings.monthly_reset_day
    # time-based via SettingsKV
    vf = db.get(SettingsKV, f"quota_valid_from:{peer_id}")
    vu = db.get(SettingsKV, f"quota_valid_until:{peer_id}")
    valid_from = vf.value if vf else None
    valid_until = vu.value if vu else None
    # usage this month
    prefix = datetime.utcnow().strftime("%Y-%m-")
    rows = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id, UsageDaily.day.like(f"{prefix}%")).all()
    used_rx = sum(r.rx for r in rows)
    used_tx = sum(r.tx for r in rows)
    return QuotaDTO(
        monthly_limit_bytes=monthly_limit_bytes,
        reset_day=reset_day_val,
        valid_from=valid_from,
        valid_until=valid_until,
        used_rx=used_rx,
        used_tx=used_tx,
    )


class QuotaUpdateDTO(BaseModel):
    monthly_limit_bytes: Optional[int] = None  # set 0 or None to clear
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


@router.patch("/peers/{peer_id}/quota", response_model=QuotaDTO)
def patch_peer_quota(peer_id: int, dto: QuotaUpdateDTO, db: Session = Depends(get_db)):
    # upsert quota
    q = db.query(Quota).filter(Quota.peer_id == peer_id).first()
    if not q:
        q = Quota(peer_id=peer_id, monthly_limit_bytes=0, reset_day=settings.monthly_reset_day)
        db.add(q)
        db.flush()
    if dto.monthly_limit_bytes is not None:
        q.monthly_limit_bytes = int(dto.monthly_limit_bytes or 0)
    # store time-based in SettingsKV
    if dto.valid_from is not None:
        if dto.valid_from == "":
            kv = db.get(SettingsKV, f"quota_valid_from:{peer_id}")
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, f"quota_valid_from:{peer_id}") or SettingsKV(key=f"quota_valid_from:{peer_id}", value="")
            kv.value = dto.valid_from
            db.add(kv)
    if dto.valid_until is not None:
        if dto.valid_until == "":
            kv = db.get(SettingsKV, f"quota_valid_until:{peer_id}")
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, f"quota_valid_until:{peer_id}") or SettingsKV(key=f"quota_valid_until:{peer_id}", value="")
            kv.value = dto.valid_until
            db.add(kv)
    db.commit()
    return get_peer_quota(peer_id, db)


class MonthlySummaryPointDTO(BaseModel):
    day: str
    rx: int
    tx: int


@router.get("/summary/month", response_model=List[MonthlySummaryPointDTO])
def get_monthly_summary(db: Session = Depends(get_db)):
    """Aggregate total RX/TX per day for the last 14 days across non-demo, selected peers."""
    today = datetime.utcnow().date()
    points: List[MonthlySummaryPointDTO] = []
    for offset in range(14):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        # Join through Peer -> Router so we can filter out demo router and unselected peers
        rows = (
            db.query(UsageDaily)
            .join(Peer, UsageDaily.peer_id == Peer.id)
            .join(Router, Peer.router_id == Router.id)
            .filter(UsageDaily.day == day)
            .filter(Peer.selected == True)
            .filter(Router.host != "demo")
            .filter(Router.name != "demo")
            .all()
        )
        total_rx = sum(r.rx for r in rows)
        total_tx = sum(r.tx for r in rows)
        points.append(MonthlySummaryPointDTO(day=day, rx=total_rx, tx=total_tx))
    # Return ascending by day
    points.sort(key=lambda p: p.day)
    return points


@router.post("/admin/purge_usage")
def purge_usage(db: Session = Depends(get_db)):
    """Delete all usage samples and rollups, keep peers/routers/settings."""
    deleted_samples = db.query(UsageSample).delete()
    deleted_daily = db.query(UsageDaily).delete()
    deleted_monthly = db.query(UsageMonthly).delete()
    db.commit()
    return {
        "ok": True,
        "deleted_samples": deleted_samples,
        "deleted_daily": deleted_daily,
        "deleted_monthly": deleted_monthly,
    }


@router.post("/admin/purge_peers")
def purge_peers(db: Session = Depends(get_db)):
    """Delete all peers (and cascading usage/quotas), keep routers/settings."""
    deleted_peers = db.query(Peer).delete()
    # Cascades will remove usage + quotas via FK; ensure rollups are cleared
    db.query(UsageSample).delete()
    db.query(UsageDaily).delete()
    db.query(UsageMonthly).delete()
    db.query(Quota).delete()
    db.commit()
    return {"ok": True, "deleted_peers": deleted_peers}

