#!/usr/bin/env python3
"""CLI: diagnose Telegram usage charts vs web panel (bindings, usage tables, TG-equivalent series)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Repo root on sys.path (run from anywhere)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Default working directory so sqlite relative paths resolve like the app
try:
    os.chdir(_ROOT)
except OSError:
    pass

os.environ.setdefault("PYTHONPATH", str(_ROOT))

from backend.db import SessionLocal  # noqa: E402
from backend.diagnose_tg_usage_charts import build_diagnosis_report  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compare Telegram-bound peer_ids to usage data (same logic as TG charts / GET /peers/{id}/usage)."
    )
    p.add_argument(
        "--router",
        default="",
        help="Substring match on router name (case-insensitive); empty = all routers",
    )
    p.add_argument(
        "--names",
        default="",
        help="Comma-separated substrings; peer must match any (case-insensitive). Empty = no name filter",
    )
    p.add_argument(
        "--telegram-user-id",
        type=int,
        default=None,
        help="Telegram platform user id (telegram_users.telegram_user_id). Omit = all users",
    )
    args = p.parse_args()
    names = [x.strip() for x in args.names.split(",") if x.strip()] or None

    db = SessionLocal()
    try:
        text = build_diagnosis_report(
            db,
            router_name_substr=args.router or "",
            peer_name_filters=names,
            telegram_telegram_user_id=args.telegram_user_id,
        )
    finally:
        db.close()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
