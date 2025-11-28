from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

from .settings import settings
from .db import SessionLocal
from .models import Router, Peer, UsageSample, UsageDaily, UsageMonthly, SettingsKV
from .routeros.factory import make_client


_scheduler: Optional[BackgroundScheduler] = None


def _poll_once():
    """Poll selected peers on all routers, store samples + daily/monthly deltas."""
    now = datetime.now(timezone.utc)
    day_key = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")

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
                    continue

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
                    ts=now,
                    rx=lp.rx_bytes,
                    tx=lp.tx_bytes,
                    endpoint=lp.endpoint or "",
                )
                db.add(sample)

                # Compute deltas (guard against counter resets)
                delta_rx = 0
                delta_tx = 0
                if last_sample is not None:
                    if lp.rx_bytes >= last_sample.rx:
                        delta_rx = lp.rx_bytes - last_sample.rx
                    if lp.tx_bytes >= last_sample.tx:
                        delta_tx = lp.tx_bytes - last_sample.tx

                if delta_rx == 0 and delta_tx == 0:
                    continue

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
                monthly = (
                    db.query(UsageMonthly)
                    .filter(UsageMonthly.peer_id == peer.id, UsageMonthly.month_key == month_key)
                    .first()
                )
                if monthly is None:
                    monthly = UsageMonthly(peer_id=peer.id, month_key=month_key, rx=delta_rx, tx=delta_tx)
                    db.add(monthly)
                else:
                    monthly.rx += delta_rx
                    monthly.tx += delta_tx

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


