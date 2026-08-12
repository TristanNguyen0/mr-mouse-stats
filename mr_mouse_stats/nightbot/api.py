"""Typed wrappers over the Nightbot public command endpoints.

Two requests per channel: resolve the Twitch login to Nightbot's own
channel id, then list that channel's custom commands. Both are cached on
disk by NightbotClient, including the 404 for a channel that never
registered, so a re-run costs nothing.

Only commands whose name is a known settings command are returned. Command
text can carry API keys inside $(urlfetch ...) arguments, so the filter is
what keeps unrelated commands out of the database entirely, and every
message that does get through has had its substitution arguments stripped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Sequence

from ..http import NightbotClient
from .parse import command_hint, normalize_name, redact_variables

logger = logging.getLogger(__name__)

BOT = "nightbot"
# The human-readable page a settings observation cites as its source.
PAGE_URL = "https://nightbot.tv/t/{channel}/commands"


@dataclass(frozen=True)
class BotCommand:
    channel: str
    bot_channel_id: str
    command_id: str
    name: str  # normalized: lowercased, no leading '!'
    message: str  # redacted
    updated_at: str | None


@dataclass(frozen=True)
class ChannelResult:
    """What one channel lookup found. `registered` false means the channel
    has no Nightbot at all, which is a normal answer worth recording."""

    channel: str
    registered: bool
    bot_channel_id: str | None
    commands: list[BotCommand]


def _normalize_ts(raw: object) -> str | None:
    """Nightbot's '2026-08-07T08:58:41.061Z' in the same shape the Twitch
    capture writes, so the two sort against each other correctly."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("unparseable timestamp", extra={"fields": {"value": raw}})
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def fetch_channel(client: NightbotClient, twitch_login: str) -> ChannelResult:
    channel = twitch_login.lower()
    record = client.channel(channel)
    if not record or not record.get("_id"):
        return ChannelResult(channel, registered=False, bot_channel_id=None, commands=[])

    bot_channel_id = str(record["_id"])
    commands = []
    for raw in client.commands(bot_channel_id):
        name = normalize_name(str(raw.get("name", "")))
        if command_hint(name) is None:
            continue
        message = str(raw.get("message", "")).strip()
        if not message:
            continue
        commands.append(
            BotCommand(
                channel=channel,
                bot_channel_id=bot_channel_id,
                command_id=str(raw.get("_id", "")),
                name=name,
                message=redact_variables(message),
                updated_at=_normalize_ts(raw.get("updatedAt")),
            )
        )
    return ChannelResult(channel, True, bot_channel_id, commands)


def fetch_channels(
    client: NightbotClient, twitch_logins: Sequence[str]
) -> Iterator[ChannelResult]:
    """One result per channel, in order. Yields as it goes rather than
    returning a list: a full roster is a few hundred rate-gated requests,
    and the caller should be able to persist progressively."""
    for login in dict.fromkeys(l.lower() for l in twitch_logins):
        yield fetch_channel(client, login)
