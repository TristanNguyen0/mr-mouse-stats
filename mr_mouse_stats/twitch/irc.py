"""Read-only anonymous Twitch IRC client.

Hard constraint, enforced structurally: this client can never send a chat
message. The single outbound path (_send) allows only the protocol verbs
NICK / CAP / JOIN / PONG — there is no PRIVMSG code path, mirroring how
LiquipediaClient is the only HTTP path.

Connection model: TLS to irc.chat.twitch.tv:6697 with a random justinfan
nick (no OAuth, no account), tags+commands capabilities, JOINs paced under
Twitch's 20-per-10s limit. Suspended/renamed channels fail to JOIN
*silently*, so requested vs confirmed joins are tracked and exposed.
"""

from __future__ import annotations

import logging
import random
import socket
import ssl
import time
from typing import Callable, Iterator

from .frames import ChatMessage, chat_message, parse_line

logger = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
PORT = 6697
ALLOWED_VERBS = frozenset({"NICK", "CAP", "JOIN", "PONG"})
JOIN_BATCH = 15
JOIN_INTERVAL = 10.0
MAX_BACKOFF = 60.0


class ProtocolViolation(RuntimeError):
    """Attempted to send a line this client must never send."""


def _default_connect() -> socket.socket:
    ctx = ssl.create_default_context()
    raw = socket.create_connection((HOST, PORT), timeout=15)
    return ctx.wrap_socket(raw, server_hostname=HOST)


class ReadOnlyIrcClient:
    def __init__(
        self,
        channels: list[str],
        nick: str | None = None,
        connect: Callable[[], socket.socket] = _default_connect,
        sleep: Callable[[float], None] = time.sleep,
        recv_timeout: float = 5.0,
    ) -> None:
        self.channels = [c.lstrip("#").lower() for c in channels]
        self.nick = nick or f"justinfan{random.randint(10_000, 99_999)}"
        self.confirmed_joins: set[str] = set()
        self._connect = connect
        self._sleep = sleep
        self._recv_timeout = recv_timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    @property
    def unconfirmed_joins(self) -> set[str]:
        return set(self.channels) - self.confirmed_joins

    def _send(self, line: str) -> None:
        verb = line.split(" ", 1)[0].upper()
        if verb not in ALLOWED_VERBS:
            raise ProtocolViolation(
                f"read-only client refuses to send {verb!r} — "
                "only NICK/CAP/JOIN/PONG are ever allowed"
            )
        assert self._sock is not None
        self._sock.sendall((line + "\r\n").encode())

    def _handshake(self) -> None:
        self._buf = b""
        self.confirmed_joins = set()
        self._sock = self._connect()
        self._sock.settimeout(self._recv_timeout)
        self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send(f"NICK {self.nick}")
        for start in range(0, len(self.channels), JOIN_BATCH):
            if start:
                self._sleep(JOIN_INTERVAL)
            batch = self.channels[start : start + JOIN_BATCH]
            self._send("JOIN " + ",".join(f"#{c}" for c in batch))
        logger.info(
            "connected to twitch irc",
            extra={"fields": {"nick": self.nick, "channels": len(self.channels)}},
        )

    def _read_lines(self) -> Iterator[str]:
        """Yield complete lines; yields nothing on a recv timeout."""
        assert self._sock is not None
        try:
            data = self._sock.recv(65536)
        except (TimeoutError, socket.timeout):
            return
        if not data:
            raise ConnectionResetError("server closed connection")
        self._buf += data
        while b"\r\n" in self._buf:
            raw, self._buf = self._buf.split(b"\r\n", 1)
            yield raw.decode("utf-8", errors="replace")

    def run(self) -> Iterator[ChatMessage | None]:
        """Yield chat messages forever, reconnecting with backoff on errors.

        Yields None as a heartbeat when the socket is quiet so callers can do
        periodic bookkeeping (duration limits, join reports) without blocking.
        """
        backoff = 1.0
        while True:
            try:
                self._handshake()
                backoff = 1.0
                while True:
                    got_line = False
                    for raw in self._read_lines():
                        got_line = True
                        message = self._handle_line(raw)
                        if message is not None:
                            yield message
                    if not got_line:
                        yield None
            except OSError as exc:
                logger.warning(
                    "twitch irc disconnected; reconnecting",
                    extra={"fields": {"error": str(exc), "backoff": backoff}},
                )
                self._close()
                self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _handle_line(self, raw: str) -> ChatMessage | None:
        line = parse_line(raw)
        if line is None:
            return None
        if line.command == "PING":
            self._send("PONG :" + (line.params[0] if line.params else "tmi.twitch.tv"))
            return None
        if line.command == "RECONNECT":
            raise ConnectionResetError("server requested reconnect")
        if line.command == "JOIN" and line.prefix_nick == self.nick and line.params:
            self.confirmed_joins.add(line.params[0].lstrip("#").lower())
            return None
        return chat_message(line)

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
