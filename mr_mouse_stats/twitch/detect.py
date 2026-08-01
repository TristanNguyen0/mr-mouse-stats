"""Identify settings-command triggers and bot accounts.

Bots are detected two ways: a list of well-known chatbot logins, and the
bot-badge tag Twitch attaches to registered bots (observed on Nightbot in
real captures). Channel-specific custom bots will be missed until added.
"""

from __future__ import annotations

import re

TRIGGER_COMMANDS = (
    "dpi",
    "edpi",
    "sens",
    "sensitivity",
    "mouse",
    "settings",
    "gear",
)

_TRIGGER_RE = re.compile(
    r"^!(" + "|".join(TRIGGER_COMMANDS) + r")\b", re.IGNORECASE
)

KNOWN_BOT_LOGINS = frozenset({
    "nightbot",
    "streamelements",
    "fossabot",
    "moobot",
    "wizebot",
    "botrix",
    "sery_bot",
    "streamlabs",
})


def trigger_command(text: str) -> str | None:
    """Return the normalized command name if text is a settings command."""
    match = _TRIGGER_RE.match(text.strip())
    return match.group(1).lower() if match else None


def is_bot(login: str, badges: tuple[str, ...] = ()) -> bool:
    if login.lower() in KNOWN_BOT_LOGINS:
        return True
    return any(badge.startswith("bot-badge/") for badge in badges)
