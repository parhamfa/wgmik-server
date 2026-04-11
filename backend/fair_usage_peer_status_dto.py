"""Fair-usage peer status DTOs and builder — shared by the REST API and Telegram image."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from .fair_usage_sync import evaluate_fair_usage_chain, get_applicable_fair_usage_rules, is_rule_over_quota
from .fair_usage_tiers import active_tier_for_combined_usage, ordered_tiers_for_rule
from .fair_usage_usage import (
    compute_next_reset_utc_for_rule,
    format_scope_label,
    normalize_scope_period,
    peer_scope_usage_for_rule,
)
from .models import FairUsageState, FairUsageRule, Peer


class FairUsageTierStatusDTO(BaseModel):
    tier_id: int
    sort_order: int
    threshold_bytes: int
    name: str = ""
    throttle_download_kbps: int
    throttle_upload_kbps: int
    is_active: bool


class FairUsageRuleStatusItemDTO(BaseModel):
    rule_id: int
    rule_name: str
    quota_mode: str
    download_quota_bytes: int
    upload_quota_bytes: Optional[int] = None
    throttle_download_kbps: int = 0
    throttle_upload_kbps: int = 0
    time_scope: Optional[str] = None
    scope_period_count: int = 1
    scope_period_unit: str = "month"
    scope_label: str = ""
    scope_type: Optional[str] = None
    sort_order: int = 0
    passthrough: bool = False
    used_rx: int = 0
    used_tx: int = 0
    over_quota: bool = False
    is_effective: bool = False
    next_reset: Optional[str] = None
    tiered: bool = False
    tiers: List[FairUsageTierStatusDTO] = []


class FairUsagePeerStatusDTO(BaseModel):
    peer_id: int
    rules: List[FairUsageRuleStatusItemDTO] = []
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None
    quota_mode: Optional[str] = None
    download_quota_bytes: int = 0
    upload_quota_bytes: Optional[int] = None
    throttle_download_kbps: int = 0
    throttle_upload_kbps: int = 0
    time_scope: Optional[str] = None
    scope_period_count: int = 1
    scope_period_unit: str = "month"
    scope_label: str = "Monthly"
    scope_type: Optional[str] = None
    sort_order: int = 0
    passthrough: bool = False
    used_rx: int = 0
    used_tx: int = 0
    throttled: bool = False
    throttled_at: Optional[str] = None
    next_reset: Optional[str] = None


def build_fair_usage_peer_status_dto(db: Session, peer: Peer) -> FairUsagePeerStatusDTO:
    """Build the same structure as GET /api/fair-usage/peers/{id}/status."""
    applicable = get_applicable_fair_usage_rules(peer, db)
    if not applicable:
        return FairUsagePeerStatusDTO(peer_id=peer.id)

    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()
    throttled = state.throttled if state else False
    throttled_at: Optional[str] = None
    if state and state.throttled_at:
        t = state.throttled_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        throttled_at = t.isoformat()
    winner = evaluate_fair_usage_chain(db, peer)
    winning_rule_id = winner[0].id if winner else None

    items: List[FairUsageRuleStatusItemDTO] = []
    for rule in applicable:
        urx, utx = peer_scope_usage_for_rule(peer.id, rule, db)
        cnt, unit = normalize_scope_period(rule)
        nxt = compute_next_reset_utc_for_rule(rule, db)
        oq = is_rule_over_quota(urx, utx, rule, db)
        tier_status: List[FairUsageTierStatusDTO] = []
        if rule.tiered:
            tiers = ordered_tiers_for_rule(db, rule.id)
            combined = urx + utx
            active = active_tier_for_combined_usage(tiers, combined)
            for t in tiers:
                tier_status.append(
                    FairUsageTierStatusDTO(
                        tier_id=t.id,
                        sort_order=t.sort_order,
                        threshold_bytes=t.threshold_bytes,
                        name=t.name or "",
                        throttle_download_kbps=t.throttle_download_kbps,
                        throttle_upload_kbps=t.throttle_upload_kbps,
                        is_active=active is not None and active.id == t.id,
                    )
                )
        items.append(
            FairUsageRuleStatusItemDTO(
                rule_id=rule.id,
                rule_name=rule.name,
                quota_mode=rule.quota_mode,
                download_quota_bytes=rule.download_quota_bytes,
                upload_quota_bytes=rule.upload_quota_bytes,
                throttle_download_kbps=rule.throttle_download_kbps,
                throttle_upload_kbps=rule.throttle_upload_kbps,
                time_scope=rule.time_scope,
                scope_period_count=cnt,
                scope_period_unit=unit,
                scope_label=format_scope_label(cnt, unit),
                scope_type=rule.scope_type,
                sort_order=rule.sort_order,
                passthrough=rule.passthrough,
                used_rx=urx,
                used_tx=utx,
                over_quota=oq,
                is_effective=winning_rule_id == rule.id,
                next_reset=nxt.isoformat(),
                tiered=rule.tiered,
                tiers=tier_status,
            )
        )

    primary_rule = winner[0] if winner else applicable[0]
    primary_tier = winner[1] if winner else None
    ur0, ut0 = peer_scope_usage_for_rule(peer.id, primary_rule, db)
    c0, u0 = normalize_scope_period(primary_rule)
    n0 = compute_next_reset_utc_for_rule(primary_rule, db)
    throttle_download = primary_tier.throttle_download_kbps if primary_tier else primary_rule.throttle_download_kbps
    throttle_upload = primary_tier.throttle_upload_kbps if primary_tier else primary_rule.throttle_upload_kbps

    return FairUsagePeerStatusDTO(
        peer_id=peer.id,
        rules=items,
        rule_id=primary_rule.id,
        rule_name=primary_rule.name,
        quota_mode=primary_rule.quota_mode,
        download_quota_bytes=primary_rule.download_quota_bytes,
        upload_quota_bytes=primary_rule.upload_quota_bytes,
        throttle_download_kbps=throttle_download,
        throttle_upload_kbps=throttle_upload,
        time_scope=primary_rule.time_scope,
        scope_period_count=c0,
        scope_period_unit=u0,
        scope_label=format_scope_label(c0, u0),
        scope_type=primary_rule.scope_type,
        sort_order=primary_rule.sort_order,
        passthrough=primary_rule.passthrough,
        used_rx=ur0,
        used_tx=ut0,
        throttled=throttled,
        throttled_at=throttled_at,
        next_reset=n0.isoformat(),
    )
