"""Thin data-access layer over Postgres.

Business logic never writes SQL outside this module. All timestamps are
ISO-8601 UTC strings produced by the caller (`now_utc()` helps) and stored
as TEXT — observed_at deliberately mixes date and timestamp precision, and
both sort correctly as strings.

Schema creation is NOT done here. `connect()` opens a connection and
nothing else; migrations are a deploy-time step (`apply_migrations`, or
`mr-mouse-stats migrate`) so the runtime role never needs DDL rights and
no per-request DDL crosses the network.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .models import PlayerInfo, TournamentMeta

logger = logging.getLogger(__name__)

MIGRATIONS_PACKAGE = "mr_mouse_stats.migrations"

Connection = psycopg.Connection[dict[str, Any]]
Row = dict[str, Any]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(dsn: str) -> Connection:
    """Open a connection with dict rows. Does not touch the schema."""
    return psycopg.connect(dsn, row_factory=dict_row)


def _migration_files() -> list[tuple[str, str]]:
    """(name, sql) pairs in filename order."""
    entries = [
        entry
        for entry in resources.files(MIGRATIONS_PACKAGE).iterdir()
        if entry.name.endswith(".sql")
    ]
    return [(e.name, e.read_text()) for e in sorted(entries, key=lambda e: e.name)]


def apply_migrations(conn: Connection) -> list[str]:
    """Apply migrations not yet recorded. Returns the names applied.

    Idempotent and safe to run against an already-current database, which
    is what makes it usable as both a deploy step and a test fixture.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    already = {
        row["name"] for row in conn.execute("SELECT name FROM schema_migrations")
    }
    applied: list[str] = []
    for name, sql in _migration_files():
        if name in already:
            continue
        logger.info("applying migration", extra={"fields": {"migration": name}})
        conn.execute(sql)
        conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
        applied.append(name)
    conn.commit()
    return applied


class Store:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def upsert_tournament(
        self, liquipedia_page: str, meta: TournamentMeta, fetched_at: str
    ) -> int:
        row = self.conn.execute(
            """
            INSERT INTO tournaments
                (liquipedia_page, name, series, tier, start_date, end_date, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (liquipedia_page) DO UPDATE SET
                name = excluded.name,
                series = excluded.series,
                tier = excluded.tier,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                fetched_at = excluded.fetched_at
            RETURNING id
            """,
            (
                liquipedia_page,
                meta.name,
                meta.series,
                meta.tier,
                meta.start_date,
                meta.end_date,
                fetched_at,
            ),
        ).fetchone()
        return row["id"]

    def get_or_create_team(self, name: str) -> int:
        row = self.conn.execute(
            """
            INSERT INTO teams (name) VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = excluded.name
            RETURNING id
            """,
            (name,),
        ).fetchone()
        return row["id"]

    def upsert_player_stub(
        self, liquipedia_page: str, resolution_status: str, now: str
    ) -> int:
        """Create the player row if absent; never downgrade an existing status."""
        row = self.conn.execute(
            """
            INSERT INTO players
                (liquipedia_page, resolution_status, first_seen_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (liquipedia_page) DO UPDATE SET updated_at = excluded.updated_at
            RETURNING id
            """,
            (liquipedia_page, resolution_status, now, now),
        ).fetchone()
        return row["id"]

    def update_player_resolved(self, db_id: int, info: PlayerInfo, now: str) -> None:
        self.conn.execute(
            """
            UPDATE players SET
                player_id = %s,
                real_name = %s,
                romanized_name = %s,
                country = %s,
                roles = %s,
                resolution_status = 'resolved',
                updated_at = %s
            WHERE id = %s
            """,
            (
                info.player_id,
                info.real_name,
                info.romanized_name,
                info.country,
                info.roles,
                now,
                db_id,
            ),
        )

    def mark_player_status(self, db_id: int, status: str, now: str) -> None:
        self.conn.execute(
            "UPDATE players SET resolution_status = %s, updated_at = %s WHERE id = %s",
            (status, now, db_id),
        )

    def upsert_roster_entry(
        self,
        tournament_id: int,
        team_id: int,
        player_db_id: int,
        role: str | None,
        is_sub: bool,
        is_staff: bool,
        played: bool | None,
        section: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO roster_entries
                (tournament_id, team_id, player_id, role, is_sub, is_staff,
                 played, section)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tournament_id, team_id, player_id) DO UPDATE SET
                role = excluded.role,
                is_sub = excluded.is_sub,
                is_staff = excluded.is_staff,
                played = excluded.played,
                section = excluded.section
            """,
            (
                tournament_id,
                team_id,
                player_db_id,
                role,
                is_sub,
                is_staff,
                played,
                section,
            ),
        )

    def record_social_account(
        self,
        player_db_id: int,
        platform: str,
        handle: str,
        url: str | None,
        observed_at: str,
        source: str = "liquipedia",
    ) -> bool:
        """Append-only: a (player, platform, handle) triple is recorded once;
        a changed handle appends a new row. Returns True if newly recorded."""
        row = self.conn.execute(
            """
            INSERT INTO social_accounts
                (player_id, platform, handle, url, source, observed_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, platform, handle) DO NOTHING
            RETURNING id
            """,
            (player_db_id, platform, handle, url, source, observed_at),
        ).fetchone()
        return row is not None

    def add_settings_observation(
        self,
        player_db_id: int,
        observed_at: str,
        source: str,
        **fields: object,
    ) -> int:
        allowed = {
            "channel", "raw_text", "dpi", "sensitivity", "windows_sens",
            "mouse_brand", "mouse_model", "pad_brand", "pad_model", "ref_url",
            "source_message_id", "source_command_id", "polling_rate", "zoom_sens",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown settings fields: {sorted(unknown)}")
        columns = ["player_id", "observed_at", "source", *fields]
        values = [player_db_id, observed_at, source, *fields.values()]
        placeholders = ", ".join("%s" for _ in values)
        row = self.conn.execute(
            f"INSERT INTO settings_observations ({', '.join(columns)}) "
            f"VALUES ({placeholders}) RETURNING id",
            values,
        ).fetchone()
        return row["id"]

    def record_twitch_message(
        self,
        msg_id: str | None,
        observed_at: str,
        channel: str,
        login: str,
        display_name: str | None,
        user_id: str | None,
        badges: str | None,
        kind: str,
        text: str,
        trigger_id: int | None = None,
    ) -> int | None:
        """Append a raw chat capture. Returns None when the Twitch message
        uuid was already recorded (reconnect overlap)."""
        row = self.conn.execute(
            """
            INSERT INTO twitch_messages
                (msg_id, observed_at, channel, login, display_name, user_id,
                 badges, kind, trigger_id, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (msg_id) DO NOTHING
            RETURNING id
            """,
            (msg_id, observed_at, channel, login, display_name, user_id,
             badges, kind, trigger_id, text),
        ).fetchone()
        return row["id"] if row is not None else None

    def unparsed_response_messages(self) -> list[Row]:
        """Candidate responses with no derived settings observation yet."""
        return self.conn.execute(
            """
            SELECT * FROM twitch_messages tm
            WHERE tm.kind IN ('bot_response', 'broadcaster_response')
              AND tm.dismissed_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM settings_observations so
                  WHERE so.source_message_id = tm.id
              )
            ORDER BY tm.observed_at
            """
        ).fetchall()

    def player_ids_by_twitch_channel(self) -> dict[str, int]:
        """Map lowercase twitch handle -> players.id."""
        rows = self.conn.execute(
            "SELECT LOWER(handle) AS handle, player_id FROM social_accounts "
            "WHERE platform = 'twitch' AND retired_at IS NULL"
        ).fetchall()
        return {row["handle"]: row["player_id"] for row in rows}

    def retire_social_account(self, account_id: int, retired_at: str) -> None:
        self.conn.execute(
            "UPDATE social_accounts SET retired_at = %s "
            "WHERE id = %s AND retired_at IS NULL",
            (retired_at, account_id),
        )

    def upsert_channel_join_status(
        self, channel: str, confirmed: bool, checked_at: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO channel_join_status (channel, confirmed, last_checked_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (channel) DO UPDATE SET
                confirmed = excluded.confirmed,
                last_checked_at = excluded.last_checked_at
            """,
            (channel.lower(), confirmed, checked_at),
        )

    def record_bot_command(
        self,
        bot: str,
        channel: str,
        command_id: str,
        name: str,
        message: str,
        updated_at: str | None,
        first_fetched_at: str,
        bot_channel_id: str | None = None,
    ) -> int | None:
        """Append a command definition. Returns None when this exact version
        was already recorded — an unedited command re-read on every run.

        Editing the command moves the bot's updated_at, so the new text
        appends rather than colliding: that is the whole change history.
        """
        row = self.conn.execute(
            """
            INSERT INTO bot_commands
                (bot, channel, bot_channel_id, command_id, name, message,
                 updated_at, first_fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            -- Untargeted: two constraints dedupe this table, one for bots
            -- that timestamp their commands and one for bots that don't.
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (bot, channel.lower(), bot_channel_id, command_id, name, message,
             updated_at, first_fetched_at),
        ).fetchone()
        return row["id"] if row is not None else None

    def upsert_bot_channel_status(
        self,
        bot: str,
        channel: str,
        registered: bool,
        bot_channel_id: str | None,
        commands_seen: int,
        checked_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO bot_channel_status
                (bot, channel, registered, bot_channel_id, commands_seen,
                 last_checked_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (bot, channel) DO UPDATE SET
                registered = excluded.registered,
                bot_channel_id = excluded.bot_channel_id,
                commands_seen = excluded.commands_seen,
                last_checked_at = excluded.last_checked_at
            """,
            (bot, channel.lower(), registered, bot_channel_id, commands_seen,
             checked_at),
        )

    def unparsed_bot_commands(self) -> list[Row]:
        """Command versions with no derived settings observation yet."""
        return self.conn.execute(
            """
            SELECT * FROM bot_commands bc
            WHERE bc.dismissed_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM settings_observations so
                  WHERE so.source_command_id = bc.id
              )
            ORDER BY bc.updated_at, bc.id
            """
        ).fetchall()

    def dismiss_bot_command(self, command_id: int, dismissed_at: str) -> None:
        self.conn.execute(
            "UPDATE bot_commands SET dismissed_at = %s "
            "WHERE id = %s AND dismissed_at IS NULL",
            (dismissed_at, command_id),
        )

    def dismiss_twitch_message(self, message_id: int, dismissed_at: str) -> None:
        self.conn.execute(
            "UPDATE twitch_messages SET dismissed_at = %s "
            "WHERE id = %s AND dismissed_at IS NULL",
            (dismissed_at, message_id),
        )

    def resolved_players(self) -> list[Row]:
        return self.conn.execute(
            "SELECT id, liquipedia_page FROM players "
            "WHERE resolution_status = 'resolved' ORDER BY liquipedia_page"
        ).fetchall()

    def has_settings_observation(
        self, player_db_id: int, source: str, observed_at: str
    ) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM settings_observations "
            "WHERE player_id = %s AND source = %s AND observed_at = %s",
            (player_db_id, source, observed_at),
        ).fetchone()
        return row is not None

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        """Abandon the current transaction. A failed write leaves the
        connection unusable until this runs."""
        self.conn.rollback()
