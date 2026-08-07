"""Long-running collection loop: IRC client -> correlator -> raw storage.

Parsing into settings_observations is deliberately NOT done here — the
`parse-observations` command derives those from stored raw messages, so
collection never loses data to a parser bug.

Writes go through CaptureWriter, which spools and retries: a database blip
must not kill the loop, because reconnecting to Twitch costs ~40s of JOIN
pacing and every message missed in the meantime is unrecoverable.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Callable

from ..db import Store, now_utc
from .capture import Correlator
from .irc import ReadOnlyIrcClient
from .writer import CaptureWriter

logger = logging.getLogger(__name__)

JOIN_REPORT_AFTER = 60.0
# How often to re-read the handle list and join channels added since startup
# (e.g. an admin handle correction). Cheap: one indexed query, and JOINs only
# happen when something actually changed.
CHANNEL_REFRESH_INTERVAL = 300.0


def _join_new_channels(client: ReadOnlyIrcClient, store: Store) -> None:
    """Pick up handles added or corrected since the collector started.

    A handle fixed in the admin dashboard used to need a collector restart,
    which cost ~40s of JOIN pacing and any open correlation windows.
    """
    try:
        known = set(store.player_ids_by_twitch_channel())
    except Exception as exc:  # a refresh failure must never stop collection
        logger.warning(
            "channel refresh failed; keeping current channels",
            extra={"fields": {"error": str(exc)}},
        )
        return
    added = client.join(sorted(known - set(client.channels)))
    if added:
        logger.info(
            "picked up channels added since startup",
            extra={"fields": {"channels": added}},
        )


def _report_joins(client: ReadOnlyIrcClient, store: Store | None) -> None:
    missing = client.unconfirmed_joins
    if missing:
        logger.warning(
            "channels never confirmed join (suspended/renamed handle?)",
            extra={"fields": {"channels": sorted(missing)}},
        )
    if store is None:
        return
    try:
        checked_at = now_utc()
        for channel in client.channels:
            store.upsert_channel_join_status(
                channel, channel not in missing, checked_at
            )
        store.commit()
    except Exception as exc:  # health reporting is not worth losing the loop
        store.rollback()
        logger.warning(
            "could not record join status",
            extra={"fields": {"error": str(exc)}},
        )


def collect(
    client: ReadOnlyIrcClient,
    store: Store | None,
    duration: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> Counter:
    """Run collection until `duration` seconds pass (0 = forever), the
    caller interrupts, or `should_stop()` goes true (SIGTERM on Fargate).

    store=None means dry run: log events, write nothing.
    """
    correlator = Correlator()
    writer = CaptureWriter(store, clock=clock) if store is not None else None
    stats: Counter = Counter()
    start = clock()
    join_reported = False
    last_refresh = start

    for item in client.run():
        now = clock()
        if duration and now - start >= duration:
            break
        if should_stop():
            logger.info("stop requested; draining")
            break
        if writer is not None:
            writer.flush()  # retry anything a previous failure left spooled
        if not join_reported and now - start >= JOIN_REPORT_AFTER:
            join_reported = True
            _report_joins(client, store)
        if store is not None and now - last_refresh >= CHANNEL_REFRESH_INTERVAL:
            last_refresh = now
            _join_new_channels(client, store)
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
                        "dry_run": writer is None,
                    }
                },
            )
            if writer is not None:
                writer.submit(event)

    if writer is not None:
        # Final drain: better to block briefly on shutdown than lose captures.
        writer.flush(force=True)
        stats["written"] = writer.written
        stats["unwritten"] = writer.pending
        stats["dropped"] = writer.dropped
        if writer.pending:
            logger.error(
                "exiting with unwritten captures",
                extra={"fields": {"pending": writer.pending}},
            )
    return stats
