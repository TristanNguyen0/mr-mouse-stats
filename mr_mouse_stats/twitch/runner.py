"""Long-running collection loop: IRC client -> correlator -> raw storage.

Parsing into settings_observations is deliberately NOT done here — the
`parse-observations` command derives those from stored raw messages, so
collection never loses data to a parser bug.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Callable

from ..db import Store, now_utc
from .capture import KIND_TRIGGER, Correlator
from .frames import ChatMessage
from .irc import ReadOnlyIrcClient

logger = logging.getLogger(__name__)

JOIN_REPORT_AFTER = 60.0
_TRIGGER_MAP_MAX = 1000


def collect(
    client: ReadOnlyIrcClient,
    store: Store | None,
    duration: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
) -> Counter:
    """Run collection until `duration` seconds pass (0 = forever) or the
    caller interrupts. store=None means dry run: log events, write nothing."""
    correlator = Correlator()
    trigger_row_ids: dict[ChatMessage, int] = {}
    stats: Counter = Counter()
    start = clock()
    join_reported = False

    for item in client.run():
        now = clock()
        if duration and now - start >= duration:
            break
        if not join_reported and now - start >= JOIN_REPORT_AFTER:
            join_reported = True
            missing = client.unconfirmed_joins
            if missing:
                logger.warning(
                    "channels never confirmed join (suspended/renamed handle?)",
                    extra={"fields": {"channels": sorted(missing)}},
                )
            if store is not None:
                checked_at = now_utc()
                for channel in client.channels:
                    store.upsert_channel_join_status(
                        channel, channel not in missing, checked_at
                    )
                store.commit()
        if item is None:
            continue
        stats["messages_seen"] += 1
        for event in correlator.feed(item):
            stats[event.kind] += 1
            logger.info(
                "capture event",
                extra={
                    "fields": {
                        "kind": event.kind,
                        "channel": event.message.channel,
                        "login": event.message.login,
                        "text": event.message.text,
                        "command": event.command,
                        "dry_run": store is None,
                    }
                },
            )
            if store is None:
                continue
            row_id = store.record_twitch_message(
                msg_id=event.message.msg_id,
                observed_at=event.message.observed_at,
                channel=event.message.channel,
                login=event.message.login,
                display_name=event.message.display_name,
                user_id=event.message.user_id,
                badges=",".join(event.message.badges),
                kind=event.kind,
                text=event.message.text,
                trigger_id=(
                    trigger_row_ids.get(event.trigger) if event.trigger else None
                ),
            )
            if row_id is not None and event.kind == KIND_TRIGGER:
                if len(trigger_row_ids) >= _TRIGGER_MAP_MAX:
                    trigger_row_ids.clear()
                trigger_row_ids[event.message] = row_id
            store.commit()
    return stats
