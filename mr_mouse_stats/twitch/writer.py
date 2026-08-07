"""Durable write path for chat captures.

Twitch chat cannot be re-fetched. A `!dpi` response that scrolls past while
the database is unreachable is gone permanently — unlike Liquipedia, which
can be re-scraped forever. So this module exists to make the failure mode
"delayed writes" rather than "lost observations":

- captures are spooled in memory and flushed as a batch;
- a failed flush rolls back, backs off, and *keeps* the spool;
- a database error never escapes to the collection loop, because staying
  connected to Twitch and retrying beats disconnecting and losing more.

The spool is bounded. Past the cap the oldest entries are dropped, loudly —
memory exhaustion would kill the process and lose everything pending.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

from ..db import Store
from .capture import KIND_TRIGGER, CaptureEvent
from .frames import ChatMessage

logger = logging.getLogger(__name__)

DEFAULT_MAX_SPOOL = 10_000
RETRY_BASE = 1.0
RETRY_MAX = 60.0
_TRIGGER_MAP_MAX = 1000


class CaptureWriter:
    """Batches captures into the database, retrying on failure."""

    def __init__(
        self,
        store: Store,
        max_spool: int = DEFAULT_MAX_SPOOL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._max_spool = max_spool
        self._clock = clock
        self._spool: deque[CaptureEvent] = deque()
        self._trigger_ids: dict[ChatMessage, int] = {}
        self._backoff = 0.0
        self._next_attempt = 0.0
        self.dropped = 0
        self.written = 0

    @property
    def pending(self) -> int:
        return len(self._spool)

    def submit(self, event: CaptureEvent) -> None:
        """Queue a capture and opportunistically flush."""
        if len(self._spool) >= self._max_spool:
            self._spool.popleft()
            self.dropped += 1
            logger.error(
                "capture spool full; dropped oldest capture",
                extra={"fields": {"dropped_total": self.dropped,
                                  "spool": self._max_spool}},
            )
        self._spool.append(event)
        self.flush()

    def flush(self, force: bool = False) -> int:
        """Try to write everything spooled. Returns the number written.

        Never raises: a flush failure is logged and retried later.
        """
        if not self._spool:
            return 0
        now = self._clock()
        if not force and now < self._next_attempt:
            return 0

        batch = list(self._spool)
        # Trigger ids must resolve within the batch too, so track them
        # locally; INSERT ... RETURNING gives the id before the commit.
        local = dict(self._trigger_ids)
        try:
            for event in batch:
                trigger_id = local.get(event.trigger) if event.trigger else None
                row_id = self._store.record_twitch_message(
                    msg_id=event.message.msg_id,
                    observed_at=event.message.observed_at,
                    channel=event.message.channel,
                    login=event.message.login,
                    display_name=event.message.display_name,
                    user_id=event.message.user_id,
                    badges=",".join(event.message.badges),
                    kind=event.kind,
                    text=event.message.text,
                    trigger_id=trigger_id,
                )
                if row_id is not None and event.kind == KIND_TRIGGER:
                    local[event.message] = row_id
            self._store.commit()
        except Exception as exc:
            self._store.rollback()
            self._backoff = min(
                RETRY_MAX, RETRY_BASE if not self._backoff else self._backoff * 2
            )
            self._next_attempt = now + self._backoff
            logger.warning(
                "capture write failed; retrying with the spool intact",
                extra={"fields": {"error": str(exc), "pending": len(self._spool),
                                  "retry_in": self._backoff}},
            )
            return 0

        for _ in batch:
            self._spool.popleft()
        self._trigger_ids = local
        if len(self._trigger_ids) > _TRIGGER_MAP_MAX:
            self._trigger_ids.clear()
        if self._backoff:
            logger.info(
                "capture writes recovered",
                extra={"fields": {"flushed": len(batch)}},
            )
        self._backoff = 0.0
        self._next_attempt = 0.0
        self.written += len(batch)
        return len(batch)
