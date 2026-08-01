"""IRCv3 frame parsing for Twitch chat (tags + prefix + command + params).

Pure functions over raw lines; no sockets here. Timestamps come from the
server's tmi-sent-ts tag (epoch milliseconds), not the local clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}


def unescape_tag_value(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(_TAG_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
        elif ch == "\\":
            i += 1  # lone trailing backslash is dropped per spec
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_tags(raw: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in raw.split(";"):
        if not item:
            continue
        key, sep, value = item.partition("=")
        tags[key] = unescape_tag_value(value) if sep else ""
    return tags


@dataclass(frozen=True)
class IrcLine:
    tags: dict[str, str]
    prefix: str | None
    command: str
    params: tuple[str, ...]

    @property
    def prefix_nick(self) -> str | None:
        if self.prefix is None:
            return None
        return self.prefix.split("!", 1)[0]


def parse_line(raw: str) -> IrcLine | None:
    """Parse one raw IRC line. Returns None for blank/unparseable lines."""
    rest = raw.strip("\r\n")
    if not rest.strip():
        return None
    tags: dict[str, str] = {}
    if rest.startswith("@"):
        tag_part, _, rest = rest[1:].partition(" ")
        tags = parse_tags(tag_part)
    prefix = None
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
    if not rest:
        return None
    command, _, rest = rest.partition(" ")
    if not command:
        return None
    params: list[str] = []
    while rest:
        if rest.startswith(":"):
            params.append(rest[1:])
            break
        param, _, rest = rest.partition(" ")
        params.append(param)
    return IrcLine(tags=tags, prefix=prefix, command=command, params=tuple(params))


@dataclass(frozen=True)
class ChatMessage:
    channel: str  # lowercase, no leading '#'
    login: str
    text: str
    display_name: str | None = None
    user_id: str | None = None
    msg_id: str | None = None  # Twitch message uuid; dedupe key across reconnects
    badges: tuple[str, ...] = ()
    sent_ts_ms: int | None = None  # server-side tmi-sent-ts

    @property
    def observed_at(self) -> str | None:
        if self.sent_ts_ms is None:
            return None
        return datetime.fromtimestamp(self.sent_ts_ms / 1000, timezone.utc).isoformat(
            timespec="seconds"
        )


def chat_message(line: IrcLine) -> ChatMessage | None:
    """Convert a parsed line to a ChatMessage; None for non-PRIVMSG lines."""
    if line.command != "PRIVMSG" or len(line.params) < 2:
        return None
    login = line.prefix_nick
    if not login:
        return None
    tags = line.tags
    ts_raw = tags.get("tmi-sent-ts")
    return ChatMessage(
        channel=line.params[0].lstrip("#").lower(),
        login=login.lower(),
        text=line.params[1],
        display_name=tags.get("display-name") or None,
        user_id=tags.get("user-id") or None,
        msg_id=tags.get("id") or None,
        badges=tuple(b for b in tags.get("badges", "").split(",") if b),
        sent_ts_ms=int(ts_raw) if ts_raw and ts_raw.isdigit() else None,
    )
