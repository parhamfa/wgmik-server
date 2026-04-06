"""Bilingual string tables for Telegram bot (EN / FA)."""

from __future__ import annotations

_STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": (
            "👋 Welcome.\nChoose what you want to check."
        ),
        "fa": (
            "\u0628\u0647 \u0631\u0628\u0627\u062a WGMik \u062e\u0648\u0634 \u0622\u0645\u062f\u06cc\u062f!\n"
            "\u0627\u0632 \u0645\u0646\u0648\u06cc \u0632\u06cc\u0631 \u0628\u0631\u0627\u06cc \u0645\u0634\u0627\u0647\u062f\u0647 \u067e\u06cc\u0631\u0647\u0627\u060c \u0645\u0635\u0631\u0641 \u0648 \u0648\u0636\u0639\u06cc\u062a \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f."
        ),
    },
    "welcome_signup": {
        "en": "You're all set. Your account is linked to {count} connection(s).",
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
        "en": "This account is blocked. Please contact your admin if this is unexpected.",
        "fa": "\u062d\u0633\u0627\u0628 \u0634\u0645\u0627 \u0645\u0633\u062f\u0648\u062f \u0634\u062f\u0647. \u0628\u0627 \u0645\u062f\u06cc\u0631 \u062a\u0645\u0627\u0633 \u0628\u06af\u06cc\u0631\u06cc\u062f.",
    },
    "no_peers": {
        "en": "No connections are linked to your account yet.",
        "fa": "\u0647\u06cc\u0686 \u067e\u06cc\u0631\u06cc \u0628\u0647 \u062d\u0633\u0627\u0628 \u0634\u0645\u0627 \u0645\u062a\u0635\u0644 \u0646\u06cc\u0633\u062a.",
    },
    "btn_my_peers": {
        "en": "My Connections",
        "fa": "\u067e\u06cc\u0631\u0647\u0627\u06cc \u0645\u0646",
    },
    "btn_my_connections": {
        "en": "My Connections",
        "fa": "\u067e\u06cc\u0631\u0647\u0627\u06cc \u0645\u0646",
    },
    "btn_usage": {
        "en": "Usage",
        "fa": "\u0645\u0635\u0631\u0641",
    },
    "btn_usage_history": {
        "en": "Usage History",
        "fa": "\u0633\u0627\u0628\u0642\u0647 \u0645\u0635\u0631\u0641",
    },
    "btn_status": {
        "en": "Status",
        "fa": "\u0648\u0636\u0639\u06cc\u062a",
    },
    "btn_fair_usage": {
        "en": "Fair Usage",
        "fa": "\u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647",
    },
    "btn_limits": {
        "en": "Limits",
        "fa": "\u0645\u062d\u062f\u0648\u062f\u06cc\u062a\u200c\u0647\u0627",
    },
    "btn_language": {
        "en": "Language",
        "fa": "\u0632\u0628\u0627\u0646",
    },
    "btn_notifications": {
        "en": "Notifications",
        "fa": "\u0627\u0639\u0644\u0627\u0646\u200c\u0647\u0627",
    },
    "btn_settings": {
        "en": "Settings",
        "fa": "\u062a\u0646\u0638\u06cc\u0645\u0627\u062a",
    },
    "btn_back": {
        "en": "Back",
        "fa": "\u00ab \u0628\u0627\u0632\u06af\u0634\u062a",
    },
    "btn_technical_details": {
        "en": "Technical details",
        "fa": "\u062c\u0632\u0626\u06cc\u0627\u062a \u0641\u0646\u06cc",
    },
    "btn_more_details": {
        "en": "More details",
        "fa": "\u062c\u0632\u0626\u06cc\u0627\u062a \u0628\u06cc\u0634\u062a\u0631",
    },
    "btn_simple_view": {
        "en": "Simple view",
        "fa": "\u0646\u0645\u0627\u06cc \u0633\u0627\u062f\u0647",
    },
    "btn_home": {
        "en": "Home",
        "fa": "\u062e\u0627\u0646\u0647",
    },
    "btn_back_to_connections": {
        "en": "Back to connections",
        "fa": "\u0628\u0627\u0632\u06af\u0634\u062a \u0628\u0647 \u067e\u06cc\u0631\u0647\u0627",
    },
    "btn_all_connections": {
        "en": "All connections",
        "fa": "\u0647\u0645\u0647 \u067e\u06cc\u0631\u0647\u0627",
    },
    "btn_another_connection": {
        "en": "Another connection",
        "fa": "\u067e\u06cc\u0631 \u062f\u06cc\u06af\u0631",
    },
    "btn_switch_language_to": {
        "en": "Switch language to {language}",
        "fa": "\u062a\u063a\u06cc\u06cc\u0631 \u0632\u0628\u0627\u0646 \u0628\u0647 {language}",
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
        "en": "Usage ({scope})",
        "fa": "\u0645\u0635\u0631\u0641 ({scope}):",
    },
    "fu_throttled": {
        "en": "Throttled",
        "fa": "\u0645\u062d\u062f\u0648\u062f \u0634\u062f\u0647",
    },
    "fu_ok": {
        "en": "Normal",
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
    "fu_image_title": {
        "en": "Fair usage",
        "fa": "\u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647",
    },
    "fu_no_rules_image": {
        "en": "No fair usage rules apply to this peer.",
        "fa": "\u0647\u06cc\u0686 \u0642\u0627\u0639\u062f\u0647\u200c\u0627\u06cc \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u0628\u0631\u0627\u06cc \u0627\u06cc\u0646 \u067e\u06cc\u0631 \u0646\u06cc\u0633\u062a.",
    },
    "fu_kind_combined_tiered": {
        "en": "Combined usage (tiered)",
        "fa": "\u0645\u0635\u0631\u0641 \u062a\u0631\u06a9\u06cc\u0628\u06cc (\u067e\u06cc\u0634\u200c\u0631\u0641\u062a\u0647)",
    },
    "fu_kind_combined": {
        "en": "Combined usage",
        "fa": "\u0645\u0635\u0631\u0641 \u062a\u0631\u06a9\u06cc\u0628\u06cc",
    },
    "fu_kind_independent": {
        "en": "Download / upload",
        "fa": "\u062f\u0627\u0646\u0644\u0648\u062f / \u0627\u067e\u0644\u0648\u062f",
    },
    "fu_image_caption": {
        "en": "Fair usage — {name}",
        "fa": "\u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u2014 {name}",
    },
    "status_card_caption": {
        "en": "🛡️ {name}",
        "fa": "🛡️ {name}",
    },
    "fu_sending": {
        "en": "Preparing limit cards...",
        "fa": "\u062f\u0631 \u062d\u0627\u0644 \u0633\u0627\u062e\u062a \u06a9\u0627\u0631\u062a\u200c\u0647\u0627\u06cc \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647\u2026",
    },
    "fu_no_rules_any": {
        "en": "No limit rules apply to this selection right now.",
        "fa": "\u0647\u06cc\u0686 \u0642\u0627\u0639\u062f\u0647\u200c\u0627\u06cc \u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647 \u0628\u0631\u0627\u06cc \u067e\u06cc\u0631\u0647\u0627\u06cc \u0634\u0645\u0627 \u0646\u06cc\u0633\u062a.",
    },
    "usage_sending": {
        "en": "📈 Sending usage history for {scope}...",
        "fa": "\u062f\u0631 \u062d\u0627\u0644 \u0633\u0627\u062e\u062a \u0646\u0645\u0648\u062f\u0627\u0631\u0647\u0627\u06cc \u0645\u0635\u0631\u0641 ({scope})\u2026",
    },
    "status_sending": {
        "en": "🛡️ Sending status cards...",
        "fa": "\u062f\u0631 \u062d\u0627\u0644 \u0627\u0631\u0633\u0627\u0644 \u06a9\u0627\u0631\u062a\u200c\u0647\u0627\u06cc \u0648\u0636\u0639\u06cc\u062a...",
    },
    "status_failed": {
        "en": "I couldn't generate the status cards right now.",
        "fa": "\u0627\u0644\u0627\u0646 \u0646\u062a\u0648\u0627\u0646\u0633\u062a\u0645 \u06a9\u0627\u0631\u062a\u200c\u0647\u0627\u06cc \u0648\u0636\u0639\u06cc\u062a \u0631\u0627 \u0628\u0633\u0627\u0632\u0645.",
    },
    "status_effective_rule": {
        "en": "Effective rule",
        "fa": "\u0642\u0627\u0639\u062f\u0647 \u0641\u0639\u0627\u0644",
    },
    "status_no_active_rule": {
        "en": "No active rule",
        "fa": "\u0642\u0627\u0639\u062f\u0647 \u0641\u0639\u0627\u0644\u06cc \u0648\u062c\u0648\u062f \u0646\u062f\u0627\u0631\u062f",
    },
    "status_next_reset": {
        "en": "Next reset",
        "fa": "\u0628\u0627\u0632\u0646\u0634\u0627\u0646\u06cc \u0628\u0639\u062f\u06cc",
    },
    "status_none": {
        "en": "\u2014",
        "fa": "\u2014",
    },
    "usage_chart_caption": {
        "en": "{name} — {scope}",
        "fa": "{name} \u2014 {scope}",
    },
    "peer_detail_charts_hint": {
        "en": "Use **Usage** and **Fair Usage** in the main menu for charts and quota cards.",
        "fa": "\u0628\u0631\u0627\u06cc \u0646\u0645\u0648\u062f\u0627\u0631\u0647\u0627 \u0648 \u06a9\u0627\u0631\u062a \u0633\u0647\u0645\u06cc\u0647\u060c \u0627\u0632 \u0645\u0646\u0648\u06cc \u0627\u0635\u0644\u06cc **\u0645\u0635\u0631\u0641** \u0648 **\u0645\u0635\u0631\u0641 \u0645\u0646\u0635\u0641\u0627\u0646\u0647** \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f.",
    },
    "usage_history_intro": {
        "en": "📈 Usage history\nChoose a time range.",
        "fa": "\ud83d\udcc8 \u0633\u0627\u0628\u0642\u0647 \u0645\u0635\u0631\u0641\n\u06cc\u06a9 \u0628\u0627\u0632\u0647 \u0632\u0645\u0627\u0646\u06cc \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f.",
    },
    "settings_intro": {
        "en": "⚙️ Settings\nChoose what you want to manage.",
        "fa": "\u2699\ufe0f \u062a\u0646\u0638\u06cc\u0645\u0627\u062a\n\u0645\u0648\u0631\u062f \u062f\u0644\u062e\u0648\u0627\u0647 \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f.",
    },
    "language_title": {
        "en": "🌐 Language\nChoose the language for this chat.",
        "fa": "\u0632\u0628\u0627\u0646 \u0627\u06cc\u0646 \u0686\u062a \u0631\u0627 \u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f.",
    },
    "notifications_intro": {
        "en": "🔔 Notifications\nChoose which updates you want to receive.",
        "fa": "\ud83d\udd14 \u0627\u0639\u0644\u0627\u0646\u200c\u0647\u0627\n\u0627\u0646\u062a\u062e\u0627\u0628 \u06a9\u0646\u06cc\u062f \u06a9\u062f\u0627\u0645 \u0627\u0639\u0644\u0627\u0646\u200c\u0647\u0627 \u0628\u0647 \u0634\u0645\u0627 \u0627\u0631\u0633\u0627\u0644 \u0634\u0648\u062f.",
    },
    "settings_command_hint": {
        "en": "⚙️ Use the Settings button on Home to manage language and notifications.",
        "fa": "\u0628\u0631\u0627\u06cc \u0645\u062f\u06cc\u0631\u06cc\u062a \u0632\u0628\u0627\u0646 \u0648 \u0627\u0639\u0644\u0627\u0646\u200c\u0647\u0627 \u0627\u0632 \u062f\u06a9\u0645\u0647 \u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u062f\u0631 \u062e\u0627\u0646\u0647 \u0627\u0633\u062a\u0641\u0627\u062f\u0647 \u06a9\u0646\u06cc\u062f.",
    },
    "access_denied": {
        "en": "You cannot access this connection.",
        "fa": "\u0634\u0645\u0627 \u0628\u0647 \u0627\u06cc\u0646 \u067e\u06cc\u0631 \u062f\u0633\u062a\u0631\u0633\u06cc \u0646\u062f\u0627\u0631\u06cc\u062f.",
    },
    "lang_switched": {
        "en": "Language updated.",
        "fa": "\u0632\u0628\u0627\u0646 \u0628\u0647 \u0641\u0627\u0631\u0633\u06cc \u062a\u063a\u06cc\u06cc\u0631 \u06a9\u0631\u062f.",
    },
    "settings_saved": {
        "en": "Saved.",
        "fa": "\u0630\u062e\u06cc\u0631\u0647 \u0634\u062f.",
    },
    "notif_label_quota_warning_80": {
        "en": "Usage warning (80%)",
        "fa": "\u0647\u0634\u062f\u0627\u0631 \u0645\u0635\u0631\u0641 (\u06f8\u06f0\u066a)",
    },
    "notif_label_quota_warning_90": {
        "en": "Usage warning (90%)",
        "fa": "\u0647\u0634\u062f\u0627\u0631 \u0645\u0635\u0631\u0641 (\u06f9\u06f0\u066a)",
    },
    "notif_label_quota_hit": {
        "en": "Speed reduced",
        "fa": "\u06a9\u0627\u0647\u0634 \u0633\u0631\u0639\u062a",
    },
    "notif_label_quota_lifted": {
        "en": "Speed restored",
        "fa": "\u0628\u0627\u0632\u06af\u0634\u062a \u0633\u0631\u0639\u062a",
    },
    "notif_label_daily_summary": {
        "en": "Daily summary",
        "fa": "\u062e\u0644\u0627\u0635\u0647 \u0631\u0648\u0632\u0627\u0646\u0647",
    },
    "notif_label_weekly_summary": {
        "en": "Weekly summary",
        "fa": "\u062e\u0644\u0627\u0635\u0647 \u0647\u0641\u062a\u06af\u06cc",
    },
    "notif_quota_warning": {
        "en": "{name} is at {pct}% of its limit ({used} / {total}).",
        "fa": "\u0647\u0634\u062f\u0627\u0631: \u067e\u06cc\u0631 \u00ab{name}\u00bb \u2014 \u0642\u0627\u0639\u062f\u0647 \u00ab{rule}\u00bb \u2014 {pct}\u066a \u0627\u0632 \u0633\u0647\u0645\u06cc\u0647 ({used} / {total}).",
    },
    "notif_quota_hit": {
        "en": "{name} — quota reached; speed reduced.",
        "fa": "\u00ab{name}\u00bb \u2014 \u0633\u0642\u0641 \u0633\u0647\u0645\u06cc\u0647 \u0628\u0631\u0627\u06cc \u0631\u0639\u0627\u06cc\u062a \u0645\u062d\u062f\u0648\u062f\u06cc\u062a \u06a9\u0627\u0647\u0634 \u06cc\u0627\u0641\u062a.",
    },
    "notif_quota_hit_flat": {
        "en": (
            "\U0001f40c Speed reduced\n\n"
            "\U0001f464 {name}\n"
            "\U0001f4cb Rule: {rule}\n"
            "\u26a1 {speed}\n\n"
            "\U0001f4c8 {pct}% \u00b7 {used} / {total}"
        ),
        "fa": (
            "\U0001f40c \u06a9\u0627\u0647\u0634 \u0633\u0631\u0639\u062a\n\n"
            "\U0001f464 {name}\n"
            "\U0001f4cb \u0642\u0627\u0639\u062f\u0647: {rule}\n"
            "\u26a1 {speed}\n\n"
            "\U0001f4c8 {pct}\u066a \u00b7 {used} / {total}"
        ),
    },
    "notif_quota_hit_tiered": {
        "en": (
            "\U0001f40c Speed reduced\n\n"
            "\U0001f464 {name}\n"
            "\U0001f4cb Rule: {rule}\n"
            "\U0001f4ca Tier: {tier}\n"
            "\u26a1 {speed}\n\n"
            "\U0001f4c8 {pct}% \u00b7 {used} / {total}"
        ),
        "fa": (
            "\U0001f40c \u06a9\u0627\u0647\u0634 \u0633\u0631\u0639\u062a\n\n"
            "\U0001f464 {name}\n"
            "\U0001f4cb \u0642\u0627\u0639\u062f\u0647: {rule}\n"
            "\U0001f4ca \u0633\u0637\u062d: {tier}\n"
            "\u26a1 {speed}\n\n"
            "\U0001f4c8 {pct}\u066a \u00b7 {used} / {total}"
        ),
    },
    "notif_quota_lifted": {
        "en": "{name} is back to normal speed. The limit window has reset.",
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
