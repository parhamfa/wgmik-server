from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from ..db import get_db, Base, engine, SessionLocal
from ..settings import settings
from ..scheduler import update_scheduler_interval
from ..security import SecretBox
from ..models import Router, SettingsKV, Peer, UsageDaily, UsageMonthly, UsageSample, Quota, Action, User
from ..auth import verify_password, get_password_hash, create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ..routeros.factory import make_client
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import traceback
from ipaddress import ip_network
import os
from zoneinfo import ZoneInfo

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


router = APIRouter(prefix="/api", tags=["api"])


# --- Authentication & Users ---

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    username = verify_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(creds: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == creds.username).first()
    if not user or not verify_password(creds.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    # Set HTTP-only cookie
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=False, # Set to True in production with HTTPS
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


class UserDTO(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: datetime


@router.get("/auth/me", response_model=UserDTO)
def read_users_me(current_user: User = Depends(get_current_user)):
    return user_to_dto(current_user)


def user_to_dto(u: User) -> UserDTO:
    return UserDTO(
        id=u.id, 
        username=u.username, 
        is_admin=u.is_admin, 
        created_at=u.created_at
    )


class CreateUserRequest(BaseModel):
    username: str
    password: str


@router.post("/users", response_model=UserDTO)
def create_user(req: CreateUserRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    user = User(
        username=req.username,
        hashed_password=get_password_hash(req.password),
        is_admin=True # For now only creating admins
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_dto(user)


@router.get("/users", response_model=List[UserDTO])
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    users = db.query(User).all()
    return [user_to_dto(u) for u in users]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Don't delete yourself
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
        
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    db.delete(user)
    db.commit()
    return {"ok": True}


# --- Existing Routes (Protected) ---


class SettingsDTO(BaseModel):
    poll_interval_seconds: int
    online_threshold_seconds: int
    monthly_reset_day: int
    timezone: str
    show_kind_pills: bool
    show_hw_stats: bool
    dashboard_refresh_seconds: int
    peer_default_scope_unit: str
    peer_default_scope_value: int
    dashboard_scope_unit: str
    dashboard_scope_value: int
    dashboard_filter_status: str
    dashboard_sort_by: str
    peer_refresh_seconds: int


class MetricsDTO(BaseModel):
    cpu_percent: Optional[float] = None
    load_1: Optional[float] = None
    load_5: Optional[float] = None
    load_15: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None




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
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
        "dashboard_scope_unit": "days",
        "dashboard_scope_value": 14,
        "dashboard_filter_status": "all",
        "dashboard_sort_by": "created",
        "peer_refresh_seconds": 30,
    }
    # Overlay any values persisted in SettingsKV
    for key in (
        "show_kind_pills",
        "show_hw_stats",
        "dashboard_refresh_seconds",
        "peer_default_scope_unit",
        "peer_default_scope_value",
        "dashboard_scope_unit",
        "dashboard_scope_value",
        "dashboard_filter_status",
        "dashboard_sort_by",
        "peer_refresh_seconds",
    ):
        kv = db.get(SettingsKV, key)
        if not kv:
            continue
        if key in ("dashboard_refresh_seconds", "peer_default_scope_value", "dashboard_scope_value", "peer_refresh_seconds"):
            try:
                data[key] = int(kv.value)
            except ValueError:
                continue
        elif key in ("show_kind_pills", "show_hw_stats"):
            data[key] = kv.value.lower() == "true"
        else:
            data[key] = kv.value
    # Back-compat: if dashboard_scope_days exists but new keys don't, treat it as days.
    if not db.get(SettingsKV, "dashboard_scope_unit") and not db.get(SettingsKV, "dashboard_scope_value"):
        legacy = db.get(SettingsKV, "dashboard_scope_days")
        if legacy:
            try:
                data["dashboard_scope_unit"] = "days"
                data["dashboard_scope_value"] = int(legacy.value)
            except ValueError:
                pass
    return SettingsDTO(**data)


@router.put("/settings", response_model=SettingsDTO)
def update_settings(dto: SettingsDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Persist overrides to SettingsKV (both core and UI prefs)
    overrides = {
        # Core settings
        "poll_interval_seconds": str(dto.poll_interval_seconds),
        "online_threshold_seconds": str(dto.online_threshold_seconds),
        "monthly_reset_day": str(dto.monthly_reset_day),
        "timezone": dto.timezone,
        # UI preferences
        "show_kind_pills": str(dto.show_kind_pills).lower(),
        "show_hw_stats": str(dto.show_hw_stats).lower(),
        "dashboard_refresh_seconds": str(dto.dashboard_refresh_seconds),
        "peer_default_scope_unit": dto.peer_default_scope_unit,
        "peer_default_scope_value": str(dto.peer_default_scope_value),
        "dashboard_scope_unit": dto.dashboard_scope_unit,
        "dashboard_scope_value": str(dto.dashboard_scope_value),
        "dashboard_filter_status": dto.dashboard_filter_status,
        "dashboard_sort_by": dto.dashboard_sort_by,
        "peer_refresh_seconds": str(dto.peer_refresh_seconds),
    }

    for k, v in overrides.items():
        kv = db.get(SettingsKV, k)
        if not kv:
            kv = SettingsKV(key=k, value=v)
            db.add(kv)
        else:
            kv.value = v
    
    # Update runtime config for core logic (immediate effect without restart)
    settings.poll_interval_seconds = dto.poll_interval_seconds
    settings.online_threshold_seconds = dto.online_threshold_seconds
    settings.monthly_reset_day = dto.monthly_reset_day
    settings.timezone = dto.timezone
    
    # Hot-reload scheduler interval
    try:
        update_scheduler_interval(dto.poll_interval_seconds)
    except Exception:
        pass

    db.commit()
    return get_settings(db)


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
def create_router(dto: RouterCreateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def get_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    return row


@router.put("/routers/{router_id}", response_model=RouterDTO)
def update_router(router_id: int, dto: RouterUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def delete_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    # Clear active router if it points to this router
    kv = db.get(SettingsKV, "active_router_id")
    if kv and kv.value.strip() == str(router_id):
        db.delete(kv)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/routers", response_model=list[RouterDTO])
def list_routers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Router).all()


class ActiveRouterDTO(BaseModel):
    router_id: Optional[int] = None


@router.get("/active_router", response_model=ActiveRouterDTO)
def get_active_router(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kv = db.get(SettingsKV, "active_router_id")
    if not kv or not kv.value.strip():
        return ActiveRouterDTO(router_id=None)
    try:
        rid = int(kv.value.strip())
        if rid <= 0:
            return ActiveRouterDTO(router_id=None)
        return ActiveRouterDTO(router_id=rid)
    except ValueError:
        return ActiveRouterDTO(router_id=None)


@router.post("/active_router", response_model=ActiveRouterDTO)
def set_active_router(dto: ActiveRouterDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if dto.router_id is None:
        kv = db.get(SettingsKV, "active_router_id")
        if kv:
            db.delete(kv)
            db.commit()
        return ActiveRouterDTO(router_id=None)

    rid = int(dto.router_id)
    if rid <= 0:
        raise HTTPException(status_code=400, detail="router_id must be positive")
    router = db.get(Router, rid)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")

    kv = db.get(SettingsKV, "active_router_id")
    if kv is None:
        kv = SettingsKV(key="active_router_id", value=str(rid))
        db.add(kv)
    else:
        kv.value = str(rid)
    db.commit()
    return ActiveRouterDTO(router_id=rid)


@router.get("/routers/{router_id}/interfaces", response_model=List[str])
def list_interfaces(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        return client.list_wireguard_interfaces()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


@router.get("/routers/{router_id}/interfaces/{iface}", response_model=WGInterfaceDTO)
def get_interface(router_id: int, iface: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def list_peers(router_id: int, interface: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
        if p.last_handshake and not p.disabled:
            # p.last_handshake is age in seconds since last handshake
            # Disabled peers should never be considered online
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


@router.post("/routers/{router_id}/peers/import")
def import_peers(router_id: int, items: list[PeerImportItem], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
            # Keep DB row synced to RouterOS when re-importing.
            exists.selected = it.selected
            exists.ros_id = row.ros_id or exists.ros_id
            exists.name = row.name or ""
            exists.allowed_address = row.allowed_address
            disabled = row.disabled
            if isinstance(disabled, str):
                disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
            exists.disabled = bool(disabled)
            continue
        disabled = row.disabled
        if isinstance(disabled, str):
            disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
        db.add(Peer(
            router_id=router_id,
            interface=row.interface,
            ros_id=row.ros_id,
            name=row.name or "",
            public_key=row.public_key,
            allowed_address=row.allowed_address,
            comment="",
            disabled=bool(disabled),
            selected=it.selected,
        ))
        imported += 1
    db.commit()
    return {"imported": imported}


class PeerCreateRouterDTO(BaseModel):
    interface: str
    name: str
    public_key: str
    allowed_address: str
    comment: Optional[str] = ""


@router.post("/routers/{router_id}/peers/add", response_model=PeerListDTO)
def create_router_peer(router_id: int, dto: PeerCreateRouterDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")

    # Inbound only guard
    if dto.allowed_address.strip() in ("0.0.0.0/0", "::/0"):
        raise HTTPException(status_code=400, detail="only inbound peers are allowed (address must not be 0.0.0.0/0 or ::/0)")

    # CIDR sanity (support comma-separated list)
    try:
        for part in [p.strip() for p in dto.allowed_address.split(",") if p.strip()]:
            _ = ip_network(part, strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid allowed_address format")

    client = make_client(router)

    # If DB row already exists, prevent accidental duplicates
    existing_db = db.query(Peer).filter(
        Peer.router_id == router_id,
        Peer.interface == dto.interface,
        Peer.public_key == dto.public_key,
    ).first()
    if existing_db:
        raise HTTPException(status_code=409, detail="peer with same public_key already exists in database on this interface")

    # Check if the peer already exists on RouterOS (by public key)
    try:
        live = client.list_wireguard_peers(dto.interface)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")
    live_match = next((p for p in live if p.public_key == dto.public_key), None)

    ros_id = ""
    disabled = False
    allowed_address = dto.allowed_address
    name = dto.name
    if live_match:
        # If it exists on router, don't overwrite; instead block with conflict.
        raise HTTPException(status_code=409, detail="peer with same public_key already exists on RouterOS on this interface")
    else:
        try:
            ros_id = client.add_wireguard_peer(
                interface=dto.interface,
                public_key=dto.public_key,
                allowed_address=dto.allowed_address,
                name=dto.name or "",
                comment=dto.comment or "",
                disabled=False,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"router create failed: {e}")

    row = Peer(
        router_id=router_id,
        interface=dto.interface,
        ros_id=ros_id,
        name=name or "",
        public_key=dto.public_key,
        allowed_address=allowed_address,
        comment=dto.comment or "",
        disabled=disabled,
        selected=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/routers/{router_id}/test")
def test_router_connection(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        _ = client.list_wireguard_interfaces()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


class RouterSyncResultDTO(BaseModel):
    ok: bool
    router_id: int
    interfaces: list[str]
    updated: int
    created: int
    missing: int


@router.post("/routers/{router_id}/sync", response_model=RouterSyncResultDTO)
def sync_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    One-shot DB ↔ RouterOS reconciliation for a router.
    - Updates existing DB peers (ros_id/name/allowed_address/disabled) to match RouterOS
    - Discovers new RouterOS peers into DB (selected=false)
    - Marks DB peers missing on RouterOS as selected=false
    """
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        ifaces = client.list_wireguard_interfaces()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")

    # Prefetch DB peers for this router
    db_peers = db.query(Peer).filter(Peer.router_id == router_id).all()
    by_key: dict[tuple[str, str], Peer] = {(p.interface, p.public_key): p for p in db_peers}

    seen: set[tuple[str, str]] = set()
    updated = 0
    created = 0
    now_utc = datetime.now(timezone.utc)

    for iface in ifaces:
        try:
            live = client.list_wireguard_peers(iface)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"router connection failed: {e}")
        for lp in live:
            key = (iface, lp.public_key)
            seen.add(key)
            row = by_key.get(key)
            if row:
                changed = False
                if lp.ros_id and row.ros_id != lp.ros_id:
                    row.ros_id = lp.ros_id
                    changed = True
                if (row.name or "") != (lp.name or ""):
                    row.name = lp.name or ""
                    changed = True
                if (row.allowed_address or "") != (lp.allowed_address or ""):
                    row.allowed_address = lp.allowed_address or ""
                    changed = True
                if bool(row.disabled) != bool(lp.disabled):
                    row.disabled = bool(lp.disabled)
                    changed = True
                    db.add(
                        Action(
                            peer_id=row.id,
                            ts=now_utc,
                            action="router_disable" if row.disabled else "router_enable",
                            note="Detected router state change during sync",
                        )
                    )
                if changed:
                    updated += 1
            else:
                disabled = lp.disabled
                if isinstance(disabled, str):
                    disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
                row = Peer(
                    router_id=router_id,
                    interface=iface,
                    ros_id=lp.ros_id or "",
                    name=lp.name or "",
                    public_key=lp.public_key,
                    allowed_address=lp.allowed_address,
                    comment="",
                    disabled=bool(disabled),
                    selected=False,
                )
                db.add(row)
                db.flush()
                by_key[key] = row
                created += 1
                db.add(
                    Action(
                        peer_id=row.id,
                        ts=now_utc,
                        action="router_discovered",
                        note="Discovered on router during sync (selected=false)",
                    )
                )

    # Mark missing peers as unselected (keeps history)
    missing = 0
    for row in db_peers:
        k = (row.interface, row.public_key)
        if row.interface in ifaces and k not in seen:
            if row.selected:
                row.selected = False
                missing += 1
                db.add(
                    Action(
                        peer_id=row.id,
                        ts=now_utc,
                        action="router_missing",
                        note=f"Peer not found on router during sync; unselected",
                    )
                )

    db.commit()
    return RouterSyncResultDTO(
        ok=True,
        router_id=router_id,
        interfaces=ifaces,
        updated=updated,
        created=created,
        missing=missing,
    )


#
# Demo endpoints removed (project is now RouterOS-backed only).
#


@router.get("/peers", response_model=List[PeerListDTO])
def list_saved_peers(router_id: Optional[int] = None, interface: Optional[str] = None, selected_only: bool = False, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def update_peer(peer_id: int, dto: PeerUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    if dto.selected is not None:
        row.selected = dto.selected
    if dto.disabled is not None:
        desired = bool(dto.disabled)
        # For real RouterOS peers, only flip DB state if router call succeeds.
        r = db.get(Router, row.router_id)
        if r and row.ros_id:
            client = make_client(r)
            try:
                client.set_peer_disabled(row.interface, row.ros_id, desired)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"router update failed: {e}")
        # If no backing router/ros_id, just update DB.
        row.disabled = desired
        db.add(
            Action(
                peer_id=row.id,
                ts=datetime.now(timezone.utc),
                action="manual_disable" if desired else "manual_enable",
                note="via API",
            )
        )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/peers/{peer_id}")
def delete_peer(peer_id: int, skip_router: bool = False, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    
    router_deleted = False
    # If it's a RouterOS-backed peer, delete it on the router first (unless skipped).
    r = db.get(Router, row.router_id)
    if r and row.ros_id and not skip_router:
        client = make_client(r)
        try:
            client.remove_wireguard_peer(row.interface, row.ros_id)
            router_deleted = True
        except Exception as e:
            # Don't lie: if router delete failed, keep DB record.
            raise HTTPException(status_code=502, detail=f"router delete failed: {e}")
            
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_peer_id": peer_id, "router_deleted": router_deleted}


class ActionDTO(BaseModel):
    ts: str
    action: str
    note: str


@router.get("/peers/{peer_id}/actions", response_model=List[ActionDTO])
def list_peer_actions(peer_id: int, limit: int = 25, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Keep limit sane
    limit = max(1, min(200, int(limit or 25)))
    rows = (
        db.query(Action)
        .filter(Action.peer_id == peer_id)
        .order_by(Action.ts.desc())
        .limit(limit)
        .all()
    )
    out: List[ActionDTO] = []
    for a in rows:
        ts = a.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(ActionDTO(ts=ts.isoformat(), action=a.action, note=a.note or ""))
    return out


class LastActionDTO(BaseModel):
    peer_id: int
    ts: str
    action: str
    note: str


@router.get("/actions/last", response_model=List[LastActionDTO])
def get_last_actions(peer_ids: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Return the latest Action per peer for a comma-separated peer_ids list.
    Designed for the Dashboard so we don't do N requests.
    """
    ids: list[int] = []
    for part in (peer_ids or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    ids = [i for i in ids if i > 0]
    if not ids:
        return []

    sub = (
        db.query(Action.peer_id.label("peer_id"), func.max(Action.ts).label("max_ts"))
        .filter(Action.peer_id.in_(ids))
        .group_by(Action.peer_id)
        .subquery()
    )
    rows = (
        db.query(Action)
        .join(sub, (Action.peer_id == sub.c.peer_id) & (Action.ts == sub.c.max_ts))
        .all()
    )
    by_peer: dict[int, Action] = {}
    for a in rows:
        if a.peer_id is None:
            continue
        # In case of ties, keep the first (same ts)
        by_peer.setdefault(int(a.peer_id), a)

    out: list[LastActionDTO] = []
    for pid in ids:
        a = by_peer.get(pid)
        if not a:
            continue
        ts = a.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(LastActionDTO(peer_id=pid, ts=ts.isoformat(), action=a.action, note=a.note or ""))
    return out


@router.post("/peers/{peer_id}/reconcile", response_model=PeerListDTO)
def reconcile_peer(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Apply current quota + access-window policy to a peer immediately (enable/disable on RouterOS),
    and sync DB state to the router result.
    """
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    r = db.get(Router, peer.router_id)
    client = make_client(r) if (r and peer.ros_id) else None

    # Timezone-aware window evaluation (treat stored datetime-local strings as settings.timezone)
    try:
        tz = ZoneInfo(getattr(settings, "timezone", "UTC") or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    now_tz = datetime.now(tz)

    vf = db.get(SettingsKV, f"quota_valid_from:{peer_id}")
    vu = db.get(SettingsKV, f"quota_valid_until:{peer_id}")
    window_start = None
    window_end = None
    if vf and vf.value:
        try:
            dt = datetime.fromisoformat(vf.value)
            window_start = dt if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            window_start = None
    if vu and vu.value:
        try:
            dt = datetime.fromisoformat(vu.value)
            window_end = dt if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            window_end = None

    inside_window = True
    if window_start and now_tz < window_start:
        inside_window = False
    if window_end and now_tz > window_end:
        inside_window = False

    # Quota evaluation (current month based on UTC day keys)
    q = db.query(Quota).filter(Quota.peer_id == peer_id).first()
    limit = int(q.monthly_limit_bytes or 0) if q else 0
    now_utc = datetime.now(timezone.utc)
    month_key = now_utc.strftime("%Y-%m")
    month_prefix = now_utc.strftime("%Y-%m-")
    monthly = (
        db.query(UsageMonthly)
        .filter(UsageMonthly.peer_id == peer_id, UsageMonthly.month_key == month_key)
        .first()
    )
    if monthly is not None:
        used = int((monthly.rx or 0) + (monthly.tx or 0))
    else:
        used_rx = (
            db.query(func.coalesce(func.sum(UsageDaily.rx), 0))
            .filter(UsageDaily.peer_id == peer_id, UsageDaily.day.like(f"{month_prefix}%"))
            .scalar()
            or 0
        )
        used_tx = (
            db.query(func.coalesce(func.sum(UsageDaily.tx), 0))
            .filter(UsageDaily.peer_id == peer_id, UsageDaily.day.like(f"{month_prefix}%"))
            .scalar()
            or 0
        )
        used = int(used_rx + used_tx)

    over_quota = limit > 0 and used >= limit
    outside_window = (window_start is not None or window_end is not None) and (not inside_window)
    desired_disabled = bool(over_quota or outside_window)

    if desired_disabled != bool(peer.disabled):
        # Apply on RouterOS first when possible.
        if client is not None:
            try:
                client.set_peer_disabled(peer.interface, peer.ros_id, desired_disabled)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"router update failed: {e}")
        peer.disabled = desired_disabled

        # Action log: treat this as policy enforcement, not manual.
        if desired_disabled:
            action = "quota_disable" if over_quota else "window_disable"
            note = (
                f"Reconciled: over quota used={used} limit={limit}"
                if over_quota
                else f"Reconciled: outside access window ({vf.value if vf else ''} – {vu.value if vu else ''})"
            )
        else:
            action = "quota_enable" if (not over_quota and limit == 0) else "window_enable"
            note = "Reconciled: conditions satisfied"
        db.add(Action(peer_id=peer.id, ts=now_utc, action=action, note=note))

    db.commit()
    db.refresh(peer)
    return peer


class UsagePointDTO(BaseModel):
    day: str
    rx: int
    tx: int


@router.get("/peers/{peer_id}/usage", response_model=List[UsagePointDTO])
def get_peer_usage(
    peer_id: int,
    window: str = "daily",
    seconds: Optional[int] = None,
    interval: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    window=daily: aggregate per day from UsageDaily (existing behaviour).
    window=raw: last `seconds` worth of UsageSample deltas, labelled by time of day.
    interval: if set, group samples into buckets of `interval` seconds (e.g. 60 or 3600).
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

        interval = interval if interval and interval > 0 else 0
        buckets: dict[datetime, list[int]] = {}
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
            
            # Use ISO8601 UTC timestamp so frontend can apply timezone formatting
            # If interval > 0, we bucket by time slice
            if interval > 0:
                ts = s.ts.replace(tzinfo=timezone.utc)
                ts_timestamp = ts.timestamp()
                # Floor time to nearest interval
                bucket_ts_val = ts_timestamp - (ts_timestamp % interval)
                bucket_dt = datetime.fromtimestamp(bucket_ts_val, tz=timezone.utc)
                b = buckets.setdefault(bucket_dt, [0, 0])
                b[0] += drx
                b[1] += dtx
            else:
                label = s.ts.replace(tzinfo=timezone.utc).isoformat()
                points.append(UsagePointDTO(day=label, rx=drx, tx=dtx))
        
        if interval > 0:
            for ts, (rx_sum, tx_sum) in sorted(buckets.items(), key=lambda kv: kv[0]):
                points.append(UsagePointDTO(day=ts.isoformat(), rx=rx_sum, tx=tx_sum))
                
        return points

    raise HTTPException(status_code=400, detail="window must be 'daily' or 'raw'")



@router.post("/peers/{peer_id}/reset_metrics")
def reset_peer_metrics(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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


# Quota endpoints (time-based fields stored in SettingsKV)
class QuotaDTO(BaseModel):
    monthly_limit_bytes: Optional[int] = None
    reset_day: int
    valid_from: Optional[str] = None  # ISO8601
    valid_until: Optional[str] = None  # ISO8601
    used_rx: int
    used_tx: int


@router.get("/peers/{peer_id}/quota", response_model=QuotaDTO)
def get_peer_quota(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def patch_peer_quota(peer_id: int, dto: QuotaUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def get_monthly_summary(days: int = 14, router_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Aggregate total RX/TX per day for the last N days across selected peers."""
    try:
        days = int(days)
    except Exception:
        days = 14
    days = max(1, min(180, days))
    today = datetime.utcnow().date()
    points: List[MonthlySummaryPointDTO] = []
    for offset in range(days):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        # Join through Peer -> Router so we can filter on selected peers
        q = (
            db.query(UsageDaily)
            .join(Peer, UsageDaily.peer_id == Peer.id)
            .join(Router, Peer.router_id == Router.id)
            .filter(UsageDaily.day == day)
            .filter(Peer.selected == True)
        )
        if router_id is not None:
            q = q.filter(Peer.router_id == router_id)
        rows = q.all()
        total_rx = sum(r.rx for r in rows)
        total_tx = sum(r.tx for r in rows)
        points.append(MonthlySummaryPointDTO(day=day, rx=total_rx, tx=total_tx))
    # Return ascending by day
    points.sort(key=lambda p: p.day)
    return points


class PeerUsageSummaryDTO(BaseModel):
    peer_id: int
    rx: int
    tx: int


@router.get("/summary/peers", response_model=List[PeerUsageSummaryDTO])
def get_peers_summary(days: Optional[int] = None, seconds: Optional[int] = None, router_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Aggregate total RX/TX per peer for the specified window.
    - If `days` is provided (e.g. 1, 7, 30), aggregates UsageDaily.
    - If `seconds` is provided (e.g. 3600), aggregates raw UsageSample deltas.
    - If neither, defaults to days=1.
    """
    summary: dict[int, dict[str, int]] = {}  # peer_id -> {rx, tx}

    if seconds and seconds > 0:
        # RAW WINDOW (deltas from UsageSample)
        seconds = max(60, min(7 * 24 * 3600, seconds))
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(seconds=seconds)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        
        router_filter = f"AND p.router_id = {int(router_id)}" if router_id else ""
        
        query = text(f"""
        WITH filtered_samples AS (
            SELECT 
                u.peer_id,
                u.ts,
                u.rx,
                u.tx
            FROM usage_samples u
            JOIN peers p ON u.peer_id = p.id
            WHERE u.ts >= :cutoff
              AND p.selected = 1
              {router_filter}
        ),
        deltas AS (
            SELECT
                peer_id,
                ts,
                rx,
                tx,
                LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_rx,
                LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_tx
            FROM filtered_samples
        )
        SELECT
            peer_id,
            SUM(CASE 
                WHEN prev_rx IS NULL THEN 0
                WHEN rx < prev_rx THEN rx
                ELSE rx - prev_rx 
            END) as total_rx,
            SUM(CASE 
                WHEN prev_tx IS NULL THEN 0
                WHEN tx < prev_tx THEN tx
                ELSE tx - prev_tx 
            END) as total_tx
        FROM deltas
        WHERE prev_rx IS NOT NULL
        GROUP BY peer_id
        """)

        result = db.execute(query, {"cutoff": cutoff_str})
        for r in result:
             peer_id = r[0]
             s = summary.setdefault(peer_id, {"rx": 0, "tx": 0})
             s["rx"] += int(r[1] or 0)
             s["tx"] += int(r[2] or 0)
            
    else:
        # DAILY WINDOW (UsageDaily)
        d = days if days and days > 0 else 1
        d = max(1, min(180, d))
        today = datetime.utcnow().date()
        date_strs = [(today - timedelta(days=o)).strftime("%Y-%m-%d") for o in range(d)]
        
        q = (
            db.query(UsageDaily.peer_id, UsageDaily.rx, UsageDaily.tx)
            .join(Peer, UsageDaily.peer_id == Peer.id)
            .filter(Peer.selected == True)
            .filter(UsageDaily.day.in_(date_strs))
        )
        if router_id is not None:
            q = q.filter(Peer.router_id == router_id)
            
        rows = q.all()
        for r in rows:
            s = summary.setdefault(r.peer_id, {"rx": 0, "tx": 0})
            s["rx"] += r.rx
            s["tx"] += r.tx

    return [
        PeerUsageSummaryDTO(peer_id=pid, rx=vals["rx"], tx=vals["tx"])
        for pid, vals in summary.items()
    ]

class SummaryRawPointDTO(BaseModel):
    ts: str
    rx: int
    tx: int


@router.get("/summary/raw", response_model=List[SummaryRawPointDTO])
def get_summary_raw(seconds: int = 3600, router_id: Optional[int] = None, interval: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Aggregate raw UsageSample deltas across selected peers for the last N seconds."""
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 3600
    seconds = max(60, min(7 * 24 * 3600, seconds))
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(seconds=seconds)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    interval = interval if interval and interval > 0 else 0
    if interval <= 0:
        # dynamic default
        if seconds <= 3600: interval = 60
        elif seconds <= 86400: interval = 3600
        else: interval = 6 * 3600

    # CTE to calculate deltas. 
    # Logic: 
    # 1. Filter samples by time and selected peers/router.
    # 2. Use LAG to find previous rx/tx.
    # 3. Calculate delta (handle resets/wrapping).
    # 4. Filter out first row (prev IS NULL) effectively.
    
    router_filter = f"AND p.router_id = {int(router_id)}" if router_id else ""
    
    query = text(f"""
    WITH filtered_samples AS (
        SELECT 
            u.peer_id,
            u.ts,
            u.rx,
            u.tx
        FROM usage_samples u
        JOIN peers p ON u.peer_id = p.id
        WHERE u.ts >= :cutoff
          AND p.selected = 1
          {router_filter}
    ),
    deltas AS (
        SELECT
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_tx
        FROM filtered_samples
    )
    SELECT
        CAST(strftime('%s', ts) / :interval AS INT) * :interval as bucket,
        SUM(CASE 
            WHEN prev_rx IS NULL THEN 0
            WHEN rx < prev_rx THEN rx
            ELSE rx - prev_rx 
        END) as d_rx,
        SUM(CASE 
            WHEN prev_tx IS NULL THEN 0
            WHEN tx < prev_tx THEN tx
            ELSE tx - prev_tx 
        END) as d_tx
    FROM deltas
    WHERE prev_rx IS NOT NULL
    GROUP BY bucket
    ORDER BY bucket
    """)

    result = db.execute(query, {"cutoff": cutoff_str, "interval": interval})
    rows = result.fetchall()

    out: List[SummaryRawPointDTO] = []
    for r in rows:
        bucket_ts = r[0]
        rx = r[1] or 0
        tx = r[2] or 0
        dt = datetime.fromtimestamp(bucket_ts, tz=timezone.utc)
        out.append(SummaryRawPointDTO(ts=dt.isoformat(), rx=rx, tx=tx))
        
    return out


@router.post("/admin/purge_usage")
def purge_usage(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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


class DashboardMetricsDTO(BaseModel):
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    uptime_seconds: int


@router.get("/dashboard/metrics", response_model=DashboardMetricsDTO)
def get_dashboard_metrics(db: Session = Depends(get_db)):
    # Placeholder: In a real app, use psutil to get system stats.
    # For now, return standard healthy values to prevent 404s.
    return DashboardMetricsDTO(
        cpu_percent=10.5,
        ram_percent=45.2,
        disk_percent=60.0,
        uptime_seconds=3600
    )

