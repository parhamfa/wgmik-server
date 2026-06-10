"""
Fair usage: resolve the effective rule in an ordered chain and keep RouterOS simple queues in sync.

Every poll calls apply_fair_usage_policy() so limit edits and manual queue deletes are repaired
without waiting for rule_id changes or one-off events.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import Action, FairUsageAssignment, FairUsageRule, FairUsageState, FairUsageTier, Peer
from .fair_usage_usage import peer_scope_usage_for_rule
from .fair_usage_tiers import active_tier_for_combined_usage, ordered_tiers_for_rule

FU_QUEUE_PREFIX = "wgmik-fu-"


class FairUsageRouterError(Exception):
    """Router queue operation failed (used when strict_router_errors=True, e.g. reconcile)."""


def _rule_sort_key(rule: FairUsageRule) -> tuple[int, int]:
    return (rule.sort_order or 0, rule.id)


def get_applicable_fair_usage_rules(peer: Peer, db: Session) -> list[FairUsageRule]:
    """All enabled rules that apply to this peer, returned in chain order.

    - If the peer has peer-level assignments, **only** those rules apply (no router/global pool).
    - Otherwise **all** enabled router rules for this router **plus** all enabled global rules apply.
    """
    assignments = (
        db.query(FairUsageAssignment)
        .join(FairUsageRule, FairUsageAssignment.rule_id == FairUsageRule.id)
        .filter(FairUsageAssignment.peer_id == peer.id, FairUsageRule.enabled == True)
        .all()
    )
    if assignments:
        rules = [db.get(FairUsageRule, a.rule_id) for a in assignments]
        rules = [r for r in rules if r and r.enabled]
        return sorted(rules, key=_rule_sort_key)

    by_id: dict[int, FairUsageRule] = {}
    for r in (
        db.query(FairUsageRule)
        .filter(
            FairUsageRule.scope_type == "router",
            FairUsageRule.router_id == peer.router_id,
            FairUsageRule.enabled == True,
        )
        .all()
    ):
        by_id[r.id] = r
    for r in db.query(FairUsageRule).filter(FairUsageRule.scope_type == "global", FairUsageRule.enabled == True).all():
        by_id[r.id] = r
    return sorted(by_id.values(), key=_rule_sort_key)


def get_effective_fair_usage_rule(peer: Peer, db: Session) -> Optional[FairUsageRule]:
    """Return the winning rule in the ordered chain, if any."""
    winner = evaluate_fair_usage_chain(db, peer)
    return winner[0] if winner else None


def peer_ids_with_applicable_fair_usage(db: Session, peers: list[Peer]) -> set[int]:
    """Peer IDs where `get_applicable_fair_usage_rules` is non-empty (assignment tier, else router+global)."""
    if not peers:
        return set()
    ids = [p.id for p in peers]
    out: set[int] = set()
    for (pid,) in (
        db.query(FairUsageAssignment.peer_id)
        .join(FairUsageRule, FairUsageAssignment.rule_id == FairUsageRule.id)
        .filter(FairUsageAssignment.peer_id.in_(ids), FairUsageRule.enabled == True)
        .distinct()
        .all()
    ):
        out.add(int(pid))
    remaining = [p for p in peers if p.id not in out]
    if not remaining:
        return out
    has_global = (
        db.query(FairUsageRule.id)
        .filter(FairUsageRule.scope_type == "global", FairUsageRule.enabled == True)
        .first()
        is not None
    )
    router_ids_with_rule = {
        int(rid)
        for (rid,) in db.query(FairUsageRule.router_id)
        .filter(FairUsageRule.scope_type == "router", FairUsageRule.enabled == True)
        .distinct()
        .all()
        if rid is not None
    }
    for p in remaining:
        if p.router_id in router_ids_with_rule or has_global:
            out.add(p.id)
    return out


def _over_quota(used_rx: int, used_tx: int, rule: FairUsageRule) -> bool:
    if rule.quota_mode == "combined":
        return (used_rx + used_tx) >= rule.download_quota_bytes
    over_dl = used_rx >= rule.download_quota_bytes if rule.download_quota_bytes else False
    over_ul = used_tx >= (rule.upload_quota_bytes or 0) if rule.upload_quota_bytes else False
    return over_dl or over_ul


def is_rule_over_quota(used_rx: int, used_tx: int, rule: FairUsageRule, db: Session) -> bool:
    """True when this rule matches by usage (flat quota or tiered ladder)."""
    if rule.tiered:
        tiers = ordered_tiers_for_rule(db, rule.id)
        if not tiers:
            return False
        combined = used_rx + used_tx
        return active_tier_for_combined_usage(tiers, combined) is not None
    return _over_quota(used_rx, used_tx, rule)


def _throttle_for_match(rule: FairUsageRule, tier: Optional[FairUsageTier]) -> tuple[int, int]:
    if tier:
        return tier.throttle_download_kbps, tier.throttle_upload_kbps
    return rule.throttle_download_kbps, rule.throttle_upload_kbps


def _matched_tier(rule: FairUsageRule, used_rx: int, used_tx: int, db: Session) -> Optional[FairUsageTier]:
    if not rule.tiered:
        return None
    tiers = ordered_tiers_for_rule(db, rule.id)
    if not tiers:
        return None
    return active_tier_for_combined_usage(tiers, used_rx + used_tx)


def evaluate_fair_usage_chain(
    db: Session,
    peer: Peer,
    now_utc: Optional[datetime] = None,
) -> Optional[tuple[FairUsageRule, Optional[FairUsageTier]]]:
    """
    Evaluate rules in chain order.

    - A flat rule matches when over quota.
    - A tiered rule matches when any tier is active.
    - If a matching rule has `passthrough=False`, the chain stops there.
    - If `passthrough=True`, later matching rules may override it.
    """
    rules = get_applicable_fair_usage_rules(peer, db)
    if not rules:
        return None

    winner: Optional[tuple[FairUsageRule, Optional[FairUsageTier]]] = None
    current_now = now_utc or datetime.utcnow()
    for rule in rules:
        used_rx, used_tx = peer_scope_usage_for_rule(peer.id, rule, db, current_now)
        tier = _matched_tier(rule, used_rx, used_tx, db)
        matched = tier is not None if rule.tiered else _over_quota(used_rx, used_tx, rule)
        if not matched:
            continue
        winner = (rule, tier)
        if not rule.passthrough:
            break
    return winner


def _sync_fu_queue_on_router(
    db: Session,
    peer: Peer,
    client,
    state: FairUsageState,
    now_utc: datetime,
    *,
    state_rule_id: int,
    up_limit: str,
    down_limit: str,
    log_label: str,
    entered_unthrottled: bool,
    strict_router_errors: bool,
) -> None:
    queue_name = f"{FU_QUEUE_PREFIX}{peer.name or peer.id}"
    target = peer.allowed_address or ""

    if not client or not peer.ros_id:
        # Scheduler often has no peer on router yet; reconcile without client skips quietly (same as legacy).
        if not strict_router_errors:
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=now_utc,
                    action="fu_throttle_failed",
                    note="No router client or peer not on router",
                )
            )
        return

    def add_queue() -> str:
        return client.add_simple_queue(
            name=queue_name,
            target=target,
            max_limit_up=up_limit,
            max_limit_down=down_limit,
            comment="wgmik fair-usage auto",
        )

    ros_id = (state.ros_queue_id or "").strip()
    if ros_id:
        try:
            client.update_simple_queue(ros_id, up_limit, down_limit)
            state.rule_id = state_rule_id
            state.throttled = True
            state.throttled_at = state.throttled_at or now_utc
            state.ros_queue_id = ros_id
            if entered_unthrottled:
                db.add(
                    Action(
                        peer_id=peer.id,
                        ts=now_utc,
                        action="fu_throttle",
                        note=f"Throttled: {log_label} ({up_limit}/{down_limit})",
                    )
                )
            return
        except Exception:
            try:
                client.remove_simple_queue(ros_id)
            except Exception:
                pass
            state.ros_queue_id = ""

    for q in client.list_simple_queues(FU_QUEUE_PREFIX):
        if q.get("name") == queue_name and q.get("ros_id"):
            rid = q["ros_id"]
            try:
                client.update_simple_queue(rid, up_limit, down_limit)
                state.ros_queue_id = rid
                state.rule_id = state_rule_id
                state.throttled = True
                state.throttled_at = state.throttled_at or now_utc
                if entered_unthrottled:
                    db.add(
                        Action(
                            peer_id=peer.id,
                            ts=now_utc,
                            action="fu_throttle",
                            note=f"Throttled: {log_label} ({up_limit}/{down_limit})",
                        )
                    )
                else:
                    db.add(
                        Action(
                            peer_id=peer.id,
                            ts=now_utc,
                            action="fu_queue_repaired",
                            note=f"Fair-usage queue reattached on router ({log_label})",
                        )
                    )
                return
            except Exception:
                continue

    try:
        new_id = add_queue()
        state.ros_queue_id = new_id
        state.rule_id = state_rule_id
        state.throttled = True
        state.throttled_at = state.throttled_at or now_utc
        if entered_unthrottled:
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=now_utc,
                    action="fu_throttle",
                    note=f"Throttled: {log_label} ({up_limit}/{down_limit})",
                )
            )
        else:
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=now_utc,
                    action="fu_queue_recreated",
                    note=f"Fair-usage queue recreated on router ({log_label})",
                )
            )
    except Exception as e:
        if strict_router_errors:
            raise FairUsageRouterError(f"router queue failed: {e}") from e
        db.add(
            Action(
                peer_id=peer.id,
                ts=now_utc,
                action="fu_throttle_failed",
                note=str(e),
            )
        )


def apply_fair_usage_policy(
    db: Session,
    peer: Peer,
    client,
    now_utc: datetime,
    *,
    strict_router_errors: bool = False,
) -> None:
    """
    Enforce fair usage for one peer using ordered-chain semantics.
    """
    rules = get_applicable_fair_usage_rules(peer, db)
    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()

    if not rules:
        if state and state.throttled:
            if state.ros_queue_id and peer.ros_id and client:
                try:
                    client.remove_simple_queue(state.ros_queue_id)
                except Exception:
                    pass
            db.delete(state)
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=now_utc,
                    action="fu_reset",
                    note="Rule removed or disabled; throttle lifted",
                )
            )
            try:
                from .telegram.notifications import notify_quota_lifted
                notify_quota_lifted(db, peer)
            except Exception:
                pass
        elif state:
            db.delete(state)
        return

    winner = evaluate_fair_usage_chain(db, peer, now_utc)
    if not winner:
        if state and state.throttled:
            if state.ros_queue_id and peer.ros_id and client:
                try:
                    client.remove_simple_queue(state.ros_queue_id)
                except Exception:
                    pass
            db.delete(state)
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=now_utc,
                    action="fu_reset",
                    note="Auto-reset: usage below quota for all applicable fair-usage rules",
                )
            )
            try:
                from .telegram.notifications import notify_quota_lifted
                notify_quota_lifted(db, peer)
            except Exception:
                pass
        return

    winning_rule, winning_tier = winner
    winning_down, winning_up = _throttle_for_match(winning_rule, winning_tier)
    up_limit = f"{winning_up}k"
    down_limit = f"{winning_down}k"

    def _tier_label(r: FairUsageRule, t: Optional[FairUsageTier]) -> str:
        if t and (t.name or "").strip():
            return f"{r.name} ({t.name.strip()})"
        return r.name

    log_label = _tier_label(winning_rule, winning_tier)

    if not state:
        state = FairUsageState(peer_id=peer.id, rule_id=winning_rule.id, throttled=False, ros_queue_id="")
        db.add(state)

    state.rule_id = winning_rule.id
    state.tier_id = winning_tier.id if winning_tier else None
    entered_unthrottled = not state.throttled
    _sync_fu_queue_on_router(
        db,
        peer,
        client,
        state,
        now_utc,
        state_rule_id=winning_rule.id,
        up_limit=up_limit,
        down_limit=down_limit,
        log_label=log_label,
        entered_unthrottled=entered_unthrottled,
        strict_router_errors=strict_router_errors,
    )
