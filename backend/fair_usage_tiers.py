"""Tiered fair-usage: one meter (combined RX+TX in period), multiple thresholds with strictest active tier winning."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .models import FairUsageState, FairUsageTier


def ordered_tiers_for_rule(db: Session, rule_id: int) -> list[FairUsageTier]:
    return (
        db.query(FairUsageTier)
        .filter(FairUsageTier.rule_id == rule_id)
        .order_by(FairUsageTier.threshold_bytes.asc(), FairUsageTier.sort_order.asc(), FairUsageTier.id.asc())
        .all()
    )


def active_tier_for_combined_usage(tiers: list[FairUsageTier], combined_bytes: int) -> Optional[FairUsageTier]:
    """Highest threshold that usage has reached (>=). Below all thresholds → None (not throttled by tiers)."""
    if not tiers:
        return None
    matching = [t for t in tiers if combined_bytes >= t.threshold_bytes]
    if not matching:
        return None
    return max(matching, key=lambda t: (t.threshold_bytes, t.sort_order, t.id))


def replace_rule_tiers(
    db: Session,
    rule_id: int,
    tier_rows: list[tuple[int, int, str, int, int]],
) -> None:
    """
    Replace all tiers for a rule. Each row:
    (sort_order, threshold_bytes, name, throttle_download_kbps, throttle_upload_kbps).
    """
    db.query(FairUsageState).filter(FairUsageState.rule_id == rule_id).update(
        {FairUsageState.tier_id: None},
        synchronize_session=False,
    )
    db.query(FairUsageTier).filter(FairUsageTier.rule_id == rule_id).delete(synchronize_session=False)
    for so, th, name, tdl, tul in tier_rows:
        db.add(
            FairUsageTier(
                rule_id=rule_id,
                sort_order=so,
                threshold_bytes=th,
                name=name or "",
                throttle_download_kbps=tdl,
                throttle_upload_kbps=tul,
            )
        )
