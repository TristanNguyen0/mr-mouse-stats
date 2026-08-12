"""Derive settings_observations from stored raw twitch messages.

The collector never parses (see runner.py) — this is the only path from a
raw capture to a settings row, and it is shared by the `parse-observations`
CLI command and the long-running service's timer thread. One implementation,
because a hosted deployment that derives differently from the CLI is exactly
how production ends up with rows no current parser would produce.

Two modes:

- incremental (the default): parse candidates that have no observation yet.
  Cheap, idempotent, and safe to run on a short timer.
- reparse: throw the derived rows away and rebuild them from the raw text.
  Needed because incremental never revisits a message it has already
  derived, so a parser fix reaches history only if something deletes the
  stale rows first. Raw captures are untouched, so this loses nothing.

Manual observations are not derived data and both modes leave them alone: a
message an admin has recorded by hand is skipped rather than parsed over.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..db import Store
from .settings_parse import parse_settings

logger = logging.getLogger(__name__)


def derive_observations(
    store: Store,
    *,
    reparse: bool = False,
    dry_run: bool = False,
    parse: Callable[[str], object] = parse_settings,
) -> dict[str, int]:
    """Parse candidate messages into settings_observations.

    Returns counts by outcome. The caller commits — the service and the CLI
    have different transaction lifetimes, and a dry run must not commit at
    all.
    """
    counts = {"parsed": 0, "unparseable": 0, "unknown_channel": 0, "deleted": 0}

    if reparse:
        # Counted even on a dry run, where nothing is deleted: the number is
        # what the run would replace, and reporting 0 would read as "no
        # stale rows" rather than "not touching them".
        counts["deleted"] = (
            store.derived_twitch_observation_count()
            if dry_run
            else store.delete_derived_twitch_observations()
        )
        rows = store.reparsable_response_messages()
    else:
        rows = store.unparsed_response_messages()

    channel_players = store.player_ids_by_twitch_channel()
    for row in rows:
        parsed = parse(row["text"])
        if parsed is None:
            counts["unparseable"] += 1
            continue
        player_id = channel_players.get(row["channel"])
        if player_id is None:
            counts["unknown_channel"] += 1
            logger.warning(
                "response in channel with no known player",
                extra={"fields": {"channel": row["channel"]}},
            )
            continue
        counts["parsed"] += 1
        logger.info(
            "settings observation",
            extra={
                "fields": {
                    "channel": row["channel"],
                    "dpi": parsed.dpi,
                    "sensitivity": parsed.sensitivity,
                    "mouse": parsed.mouse_brand,
                    "dry_run": dry_run,
                }
            },
        )
        if not dry_run:
            store.add_settings_observation(
                player_id,
                row["observed_at"],
                "twitch_chat",
                channel=row["channel"],
                raw_text=row["text"],
                dpi=parsed.dpi,
                sensitivity=parsed.sensitivity,
                windows_sens=parsed.windows_sens,
                mouse_brand=parsed.mouse_brand,
                mouse_model=parsed.mouse_model,
                source_message_id=row["id"],
            )
    return counts


def format_counts(counts: dict[str, int], *, dry_run: bool = False) -> str:
    """One-line human summary, shared by the CLI and the service log."""
    parts = [
        f"{counts['parsed']} observations parsed",
        f"{counts['unparseable']} candidates unparseable (kept for re-parse)",
        f"{counts['unknown_channel']} in unknown channels",
    ]
    if counts.get("deleted"):
        parts.insert(0, f"{counts['deleted']} derived rows replaced")
    return ", ".join(parts) + (" (dry run, nothing written)" if dry_run else "")
