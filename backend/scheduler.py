from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Tuple

from .settings import settings
from .db import SessionLocal
from .models import Router, Peer, UsageSample, UsageDaily, UsageMonthly, SettingsKV, Quota, Action
from .routeros.factory import make_client


_scheduler: Optional[BackgroundScheduler] = None


def _poll_once():
    """Poll selected peers on all routers, store samples + daily/monthly deltas and enforce quotas/windows."""
    now_utc = datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(getattr(settings, "timezone", "UTC") or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    day_key = now_utc.strftime("%Y-%m-%d")
    month_key = now_utc.strftime("%Y-%m")
    month_prefix = now_utc.strftime("%Y-%m-")

    db: Session = SessionLocal()
    try:
        # Fetch all selected peers and group by (router_id, interface)
        peers = db.query(Peer).filter(Peer.selected == True).all()
        if not peers:
            return

        groups: Dict[Tuple[int, str], list[Peer]] = {}
        for p in peers:
            key = (p.router_id, p.interface)
            groups.setdefault(key, []).append(p)

        router_cache: Dict[int, Router] = {}

        for (router_id, iface), group_peers in groups.items():
            router = router_cache.get(router_id)
            if router is None:
                router = db.get(Router, router_id)
                if router is None:
                    continue
                router_cache[router_id] = router
            try:
                client = make_client(router)
                live_peers = client.list_wireguard_peers(iface)
            except Exception:
                # If a router/interface is unreachable, skip this group
                continue

            live_by_pub = {lp.public_key: lp for lp in live_peers}

            for peer in group_peers:
                lp = live_by_pub.get(peer.public_key)
                if not lp:
                    # Peer missing on router: stop polling it (keep record + history)
                    if peer.selected:
                        peer.selected = False
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="router_missing",
                                note=f"Peer not found on router {router_id} iface={iface}; unselected",
                            )
                        )
                    continue

                # --- Router state reconciliation (DB <- RouterOS) ---
                # If RouterOS internal id changed (remove/re-add), keep DB in sync.
                if lp.ros_id and peer.ros_id != lp.ros_id:
                    peer.ros_id = lp.ros_id
                    db.add(
                        Action(
                            peer_id=peer.id,
                            ts=now_utc,
                            action="router_update",
                            note=f"Updated ros_id from router: {lp.ros_id}",
                        )
                    )

                # Keep basic identity fields synced (non-destructive).
                if (lp.name or "") != (peer.name or ""):
                    peer.name = lp.name or ""
                if (lp.allowed_address or "") != (peer.allowed_address or ""):
                    peer.allowed_address = lp.allowed_address or ""

                # Disabled state must reflect RouterOS. If it differs, assume external change.
                if bool(peer.disabled) != bool(lp.disabled):
                    peer.disabled = bool(lp.disabled)
                    db.add(
                        Action(
                            peer_id=peer.id,
                            ts=now_utc,
                            action="router_disable" if peer.disabled else "router_enable",
                            note="Detected router state change during poll",
                        )
                    )

                # Fetch previous sample before writing a new one
                last_sample: UsageSample | None = (
                    db.query(UsageSample)
                    .filter(UsageSample.peer_id == peer.id)
                    .order_by(UsageSample.ts.desc())
                    .first()
                )

                # Record raw sample
                sample = UsageSample(
                    peer_id=peer.id,
                    ts=now_utc,
                    rx=lp.rx_bytes,
                    tx=lp.tx_bytes,
                    endpoint=lp.endpoint or "",
                )
                db.add(sample)

                # Compute deltas (guard against counter resets)
                delta_rx = 0
                delta_tx = 0
                if last_sample is not None:
                    reset_rx = lp.rx_bytes < last_sample.rx
                    reset_tx = lp.tx_bytes < last_sample.tx
                    if reset_rx:
                        # Counter reset/recreate: best-effort count since reset
                        delta_rx = lp.rx_bytes
                    else:
                        delta_rx = lp.rx_bytes - last_sample.rx
                    if reset_tx:
                        delta_tx = lp.tx_bytes
                    else:
                        delta_tx = lp.tx_bytes - last_sample.tx
                    if reset_rx or reset_tx:
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="counter_reset",
                                note=f"Detected counter reset: rx {last_sample.rx}->{lp.rx_bytes}, tx {last_sample.tx}->{lp.tx_bytes}",
                            )
                        )
                monthly = (
                    db.query(UsageMonthly)
                    .filter(UsageMonthly.peer_id == peer.id, UsageMonthly.month_key == month_key)
                    .first()
                )

                # Update rollups only when there's actual traffic in this poll
                if delta_rx != 0 or delta_tx != 0:
                    # Update / upsert UsageDaily
                    daily = (
                        db.query(UsageDaily)
                        .filter(UsageDaily.peer_id == peer.id, UsageDaily.day == day_key)
                        .first()
                    )
                    if daily is None:
                        daily = UsageDaily(peer_id=peer.id, day=day_key, rx=delta_rx, tx=delta_tx)
                        db.add(daily)
                    else:
                        daily.rx += delta_rx
                        daily.tx += delta_tx

                    # Update / upsert UsageMonthly
                    if monthly is None:
                        monthly = UsageMonthly(peer_id=peer.id, month_key=month_key, rx=delta_rx, tx=delta_tx)
                        db.add(monthly)
                    else:
                        monthly.rx += delta_rx
                        monthly.tx += delta_tx

                def month_total_bytes() -> int:
                    # Prefer UsageMonthly (cheaper), but fall back to summing UsageDaily so UI + enforcement match.
                    if monthly is not None:
                        return int((monthly.rx or 0) + (monthly.tx or 0))
                    used_rx = (
                        db.query(func.coalesce(func.sum(UsageDaily.rx), 0))
                        .filter(UsageDaily.peer_id == peer.id, UsageDaily.day.like(f"{month_prefix}%"))
                        .scalar()
                        or 0
                    )
                    used_tx = (
                        db.query(func.coalesce(func.sum(UsageDaily.tx), 0))
                        .filter(UsageDaily.peer_id == peer.id, UsageDaily.day.like(f"{month_prefix}%"))
                        .scalar()
                        or 0
                    )
                    return int(used_rx + used_tx)

                # Quota enforcement: auto-disable peers that exceed their monthly limit
                quota = db.query(Quota).filter(Quota.peer_id == peer.id).first()
                over_quota = False
                if quota and quota.monthly_limit_bytes and quota.monthly_limit_bytes > 0:
                    total = month_total_bytes()
                    over_quota = total >= quota.monthly_limit_bytes
                    if over_quota and not peer.disabled:
                        # For real RouterOS peers, only flip DB state if router call succeeds.
                        if peer.ros_id:
                            try:
                                client.set_peer_disabled(peer.interface, peer.ros_id, True)
                                peer.disabled = True
                                db.add(
                                    Action(
                                        peer_id=peer.id,
                                        ts=now_utc,
                                        action="quota_disable",
                                        note=f"Auto-disabled: used={total} limit={quota.monthly_limit_bytes}",
                                    )
                                )
                            except Exception as e:
                                db.add(
                                    Action(
                                        peer_id=peer.id,
                                        ts=now_utc,
                                        action="quota_disable_failed",
                                        note=f"{e}",
                                    )
                                )
                        else:
                            # No RouterOS backing, just flip DB flag.
                            peer.disabled = True
                            db.add(
                                Action(
                                    peer_id=peer.id,
                                    ts=now_utc,
                                    action="quota_disable",
                                    note=f"Auto-disabled (no ros_id): used={total} limit={quota.monthly_limit_bytes}",
                                )
                            )

                # Access window enforcement: optional time-based enable/disable
                vf = db.get(SettingsKV, f"quota_valid_from:{peer.id}")
                vu = db.get(SettingsKV, f"quota_valid_until:{peer.id}")
                window_start = None
                window_end = None
                try:
                    if vf and vf.value:
                        dt = datetime.fromisoformat(vf.value)
                        window_start = dt if dt.tzinfo else dt.replace(tzinfo=tz)
                except Exception:
                    window_start = None
                try:
                    if vu and vu.value:
                        dt = datetime.fromisoformat(vu.value)
                        window_end = dt if dt.tzinfo else dt.replace(tzinfo=tz)
                except Exception:
                    window_end = None
                inside_window = True
                if window_start and now_local < window_start:
                    inside_window = False
                if window_end and now_local > window_end:
                    inside_window = False

                if (window_start or window_end) and (not inside_window) and (not peer.disabled):
                    # Outside access window -> disable
                    if peer.ros_id:
                        try:
                            client.set_peer_disabled(peer.interface, peer.ros_id, True)
                            peer.disabled = True
                            db.add(
                                Action(
                                    peer_id=peer.id,
                                    ts=now_utc,
                                    action="window_disable",
                                    note=f"Auto-disabled outside access window ({vf.value if vf else ''} – {vu.value if vu else ''})",
                                )
                            )
                        except Exception as e:
                            db.add(
                                Action(
                                    peer_id=peer.id,
                                    ts=now_utc,
                                    action="window_disable_failed",
                                    note=f"{e}",
                                )
                            )
                    else:
                        peer.disabled = True
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="window_disable",
                                note="Auto-disabled outside access window (no ros_id)",
                            )
                        )

                # Auto-enable when conditions are satisfied again, but only if we disabled it (quota/window).
                if peer.disabled and (not over_quota) and inside_window:
                    last_action = (
                        db.query(Action)
                        .filter(Action.peer_id == peer.id)
                        .order_by(Action.ts.desc())
                        .first()
                    )
                    if last_action and last_action.action in ("window_disable", "quota_disable"):
                        enable_action = "window_enable" if last_action.action == "window_disable" else "quota_enable"
                        enable_note = "Auto-enabled: inside access window" if last_action.action == "window_disable" else "Auto-enabled: not over quota"
                        if peer.ros_id:
                            try:
                                client.set_peer_disabled(peer.interface, peer.ros_id, False)
                                peer.disabled = False
                                db.add(Action(peer_id=peer.id, ts=now_utc, action=enable_action, note=enable_note))
                            except Exception as e:
                                db.add(Action(peer_id=peer.id, ts=now_utc, action=f"{enable_action}_failed", note=f"{e}"))
                        else:
                            peer.disabled = False
                            db.add(Action(peer_id=peer.id, ts=now_utc, action=enable_action, note=enable_note + " (no ros_id)"))

        db.commit()
    finally:
        db.close()


def ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        # Read persisted poll interval if present
        db: Session = SessionLocal()
        try:
            kv = db.get(SettingsKV, "poll_interval_seconds")
            interval = (
                int(kv.value)
                if kv and kv.value.isdigit() and int(kv.value) > 0
                else settings.poll_interval_seconds
            )
        finally:
            db.close()
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(
            _poll_once,
            IntervalTrigger(seconds=interval),
            id="polling-job",
            replace_existing=True,
        )
        _scheduler.start()
    return _scheduler


def update_scheduler_interval(seconds: int) -> None:
    """Hot-reload the polling interval without restarting the server."""
    global _scheduler
    if _scheduler is None or seconds <= 0:
        return
    trigger = IntervalTrigger(seconds=seconds)
    try:
        _scheduler.reschedule_job("polling-job", trigger=trigger)
    except Exception:
        # If job missing for some reason, recreate it
        _scheduler.add_job(
            _poll_once,
            trigger,
            id="polling-job",
            replace_existing=True,
        )


