from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import UsageMinute


def floor_to_minute_utc(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0, tzinfo=None)


def bulk_upsert_usage_minute(
    db: Session,
    rows: Iterable[dict[str, object]],
) -> None:
    values = [row for row in rows if int(row.get("rx", 0) or 0) != 0 or int(row.get("tx", 0) or 0) != 0]
    if not values:
        return

    stmt = sqlite_insert(UsageMinute).values(values)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=["peer_id", "minute_ts"],
        set_={
            "rx": UsageMinute.rx + excluded.rx,
            "tx": UsageMinute.tx + excluded.tx,
        },
    )
    db.execute(stmt)


def upsert_usage_minute(db: Session, peer_id: int, minute_ts: datetime, rx: int, tx: int) -> None:
    bulk_upsert_usage_minute(
        db,
        [
            {
                "peer_id": peer_id,
                "minute_ts": minute_ts,
                "rx": int(rx or 0),
                "tx": int(tx or 0),
            }
        ],
    )
