"""Correlate viewer-typed triggers with the responses they provoke.

A trigger (!dpi etc.) opens a per-channel window. Messages inside the
window from a bot — or from the broadcaster answering personally — become
capture candidates tied back to the trigger. We only ever *observe*
triggers typed by other viewers; nothing here sends anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import detect
from .frames import ChatMessage

DEFAULT_WINDOW_MS = 20_000

KIND_TRIGGER = "trigger"
KIND_BOT = "bot_response"
KIND_BROADCASTER = "broadcaster_response"


@dataclass(frozen=True)
class CaptureEvent:
    kind: str
    message: ChatMessage
    command: str | None = None  # normalized trigger command, on triggers
    trigger: ChatMessage | None = None  # the trigger this responds to


class Correlator:
    def __init__(self, window_ms: int = DEFAULT_WINDOW_MS) -> None:
        self.window_ms = window_ms
        self._windows: dict[str, tuple[ChatMessage, int]] = {}

    def feed(self, message: ChatMessage) -> list[CaptureEvent]:
        if message.sent_ts_ms is None:
            return []
        now = message.sent_ts_ms

        command = detect.trigger_command(message.text)
        if command is not None:
            self._windows[message.channel] = (message, now + self.window_ms)
            return [CaptureEvent(KIND_TRIGGER, message, command=command)]

        window = self._windows.get(message.channel)
        if window is None:
            return []
        trigger, expires = window
        if now > expires:
            del self._windows[message.channel]
            return []

        if detect.is_bot(message.login, message.badges):
            return [CaptureEvent(KIND_BOT, message, trigger=trigger)]
        if message.login == message.channel:
            return [CaptureEvent(KIND_BROADCASTER, message, trigger=trigger)]
        return []
