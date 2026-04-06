"""Legacy: custom export hostname was mirrored in the peer comment as |WGMIK:ep=<b64>.

RouterOS 7+ exposes native ``client-endpoint`` on WireGuard peers; the app uses that for new writes.
These helpers remain for reading old comments and stripping the token when saving.
"""

from __future__ import annotations

import base64
from typing import Optional

WGMIX_EP = "|WGMIK:ep="


def merge_wgmik_endpoint_in_comment(existing: Optional[str], ep: Optional[str]) -> str:
    """Strip any previous WGMIK token, then append URL-safe base64 of endpoint if non-empty."""
    raw = (existing or "").strip()
    if WGMIX_EP in raw:
        raw = raw.split(WGMIX_EP, 1)[0].rstrip()
    ep_st = (ep or "").strip()
    if not ep_st:
        return raw
    tok = base64.urlsafe_b64encode(ep_st.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{raw}{WGMIX_EP}{tok}" if raw else f"{WGMIX_EP}{tok}"


def parse_wgmik_endpoint_from_comment(comment: Optional[str]) -> Optional[str]:
    if not comment or WGMIX_EP not in comment:
        return None
    token = comment.split(WGMIX_EP, 1)[-1].strip()
    if not token:
        return None
    pad = "=" * ((4 - len(token) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(token + pad).decode("utf-8")
    except Exception:
        return None
