import re
from dataclasses import dataclass
from typing import Optional


MIN_ROUTEROS_VERSION = (7, 15)


@dataclass(frozen=True)
class RouterOSVersionCheck:
    version: str
    supported: bool


def parse_routeros_version(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    raw = (value or "").strip()
    if not raw:
        return None
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    return major, minor, patch


def is_routeros_supported(value: Optional[str]) -> bool:
    parsed = parse_routeros_version(value)
    if parsed is None:
        return False
    major, minor, _patch = parsed
    return (major, minor) >= MIN_ROUTEROS_VERSION


def assert_routeros_supported(version: Optional[str]) -> RouterOSVersionCheck:
    raw = (version or "").strip()
    supported = is_routeros_supported(raw)
    if not supported:
        min_label = ".".join(str(part) for part in MIN_ROUTEROS_VERSION)
        raise ValueError(f"RouterOS {min_label}+ is required; detected {raw or 'unknown'}")
    return RouterOSVersionCheck(version=raw, supported=True)
