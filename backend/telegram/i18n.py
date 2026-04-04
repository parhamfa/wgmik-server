"""Bilingual string tables for Telegram bot (EN / FA)."""

from __future__ import annotations

_STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": (
            "Welcome to WGMik Bot!\n"
            "Use the menu below to check your WireGuard peers, usage, and fair-usage status."
        ),
        "fa": (
            "\u0628\u0647 \u0631\u0628\u0627\u062a WGMik \u062e\u0648\u0634 \u0622\u0645\u062f\u06cc\u062f!\n"
            "\u0627\u0632 \u0645\u0646\u0648\u06cc \u0632\u06cc\u0631 \u0628\u0631\u0627\u06cc \u0645\u0634\u0627\u0647\u062f\u0647 \u067e\u06cc\u0631\u0647\u0627\u060c \u0645\u0635\u0631\u0641 \u0648 \u0648\u0636\u0639\u06cc\u062a \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f."
        ),
    },
    "welcome_signup": {
        "en": "Welcome! Your account has been linked to {count} peer(s).",
        "fa": "\u062e\u0648\u0634 \u0622\u0645\u062f\u06cc\u062f! \u062d\u0633\u0627\u0628 \u0634\u0645\u0627 \u0628\u0647 {count} \u067e\u06cc\u0631 \u0645\u062a\u0635\u0644 \u0634\u062f.",
    },
    "token_invalid": {
        "en": "This signup link is invalid or has expired.",
        "fa": "\u0627\u06cc\u0646 \u0644\u06cc\u0646\u06a9 \u062b\u0628\u062a\u200c\u0646\u0627\u0645 \u0646\u0627\u0645\u0639\u062a\u0628\u0631 \u0627\u0633\u062a \u06cc\u0627 \u0645\u0646\u0642\u0636\u06cc \u0634\u062f\u0647.",
    },
    "token_used": {
        "en": "This signup link has already been used.",
        "fa": "\u0627\u06cc\u0646 \u0644\u06cc\u0646\u06a9 \u062b\u0628\u062a\u200c\u0646\u0627\u0645 \u0642\u0628\u0644\u0627\u064b \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u0634\u062f\u0647.",
    },
    "blocked": {
        "en": "Your account has been blocked. Contact the administrator.",
        "fa": "\u062d\u0633\u0627\u0628 \u0634\u0645\u0627 \u0645\u0633\u062f\u0648\u062f \u0634\u062f\u0647. \u0628\u0627 \u0645\u062f\u06cc\u0631 \u062a\u0645\u0627\u0633 \u0628\u06af\u06cc\u0631\u06cc\u062f.",
    },
    "no_peers": {
        "en": "You have no peers linked to your account.",
        "fa": "\u0647\u06cc\u0686 \u067e\u06cc\u0631\u06cc \u0628\u0647 \u062d\u0633\u0627\u0628 \u0634\u0645\u0627 \u0645\u062a\u0635\u0644 \u0646\u06cc\u0633\u062a.",
    },
    "btn_my_peers": {
        "en": "My Peers",
        "fa": "\u067e\u06cc\u0631\u0647\u0627\u06cc \u0645\u0646",
    },
    "btn_usage": {
        "en": "Usage",
        "fa": "\u0645\u0635\u0631\u0641",
    },
    "btn_fair_usage": {
        "en": "Fair Usage",
        "fa": "\u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647",
    },
    "btn_settings": {
        "en": "Settings",
        "fa": "\u062a\u0646\u0638\u06cc\u0645\u0627\u062a",
    },
    "btn_back": {
        "en": "\u00ab Back",
        "fa": "\u00ab \u0628\u0627\u0632\u06af\u0634\u062a",
    },
    "btn_today": {
        "en": "Today",
        "fa": "\u0627\u0645\u0631\u0648\u0632",
    },
    "btn_this_week": {
        "en": "This Week",
        "fa": "\u0627\u06cc\u0646 \u0647\u0641\u062a\u0647",
    },
    "btn_this_month": {
        "en": "This Month",
        "fa": "\u0627\u06cc\u0646 \u0645\u0627\u0647",
    },
    "peer_online": {
        "en": "Online",
        "fa": "\u0622\u0646\u0644\u0627\u06cc\u0646",
    },
    "peer_offline": {
        "en": "Offline",
        "fa": "\u0622\u0641\u0644\u0627\u06cc\u0646",
    },
    "peer_disabled": {
        "en": "Disabled",
        "fa": "\u063a\u06cc\u0631\u0641\u0639\u0627\u0644",
    },
    "usage_header": {
        "en": "Usage ({scope}):",
        "fa": "\u0645\u0635\u0631\u0641 ({scope}):",
    },
    "fu_throttled": {
        "en": "Throttled",
        "fa": "\u0645\u062d\u062f\u0648\u062f \u0634\u062f\u0647",
    },
    "fu_ok": {
        "en": "OK",
        "fa": "\u0639\u0627\u062f\u06cc",
    },
    "fu_quota_line": {
        "en": "Quota: {used} / {total}",
        "fa": "\u0633\u0647\u0645\u06cc\u0647: {used} / {total}",
    },
    "fu_next_reset": {
        "en": "Resets: {time}",
        "fa": "\u0628\u0627\u0632\u0646\u0634\u0627\u0646\u06cc: {time}",
    },
    "lang_switched": {
        "en": "Language switched to English.",
        "fa": "\u0632\u0628\u0627\u0646 \u0628\u0647 \u0641\u0627\u0631\u0633\u06cc \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f.",
    },
    "notif_quota_warning": {
        "en": "Warning: peer \"{name}\" has used {pct}% of fair-usage quota ({used} / {total}).",
        "fa": "\u0647\u0634\u062f\u0627\u0631: \u067e\u06cc\u0631 \u00ab{name}\u00bb {pct}\u066a \u0627\u0632 \u0633\u0647\u0645\u06cc\u0647 \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u0631\u0627 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0631\u062f\u0647 ({used} / {total}).",
    },
    "notif_quota_hit": {
        "en": "Peer \"{name}\" has reached fair-usage quota and is now throttled.",
        "fa": "\u067e\u06cc\u0631 \u00ab{name}\u00bb \u0628\u0647 \u0633\u0642\u0641 \u0633\u0647\u0645\u06cc\u0647 \u0631\u0633\u06cc\u062f \u0648 \u0627\u06a9\u0646\u0648\u0646 \u0645\u062d\u062f\u0648\u062f \u0634\u062f\u0647.",
    },
    "notif_quota_lifted": {
        "en": "Peer \"{name}\" is no longer throttled. Quota has been reset.",
        "fa": "\u067e\u06cc\u0631 \u00ab{name}\u00bb \u062f\u06cc\u06af\u0631 \u0645\u062d\u062f\u0648\u062f \u0646\u06cc\u0633\u062a. \u0633\u0647\u0645\u06cc\u0647 \u0628\u0627\u0632\u0646\u0634\u0627\u0646\u06cc \u0634\u062f.",
    },
    "not_registered": {
        "en": "You are not registered. Ask your admin for a signup link.",
        "fa": "\u0634\u0645\u0627 \u062b\u0628\u062a\u200c\u0646\u0627\u0645 \u0646\u0634\u062f\u0647\u200c\u0627\u06cc\u062f. \u0627\u0632 \u0645\u062f\u06cc\u0631 \u0644\u06cc\u0646\u06a9 \u062b\u0628\u062a\u200c\u0646\u0627\u0645 \u0628\u06af\u06cc\u0631\u06cc\u062f.",
    },
}


def t(key: str, lang: str = "en", **kwargs: str) -> str:
    entry = _STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text
