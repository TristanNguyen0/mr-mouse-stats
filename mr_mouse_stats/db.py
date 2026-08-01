"""Thin data-access layer over SQLite.

Business logic never writes SQL outside this module, so a Postgres swap
only touches this file and schema.sql. All timestamps are ISO-8601 UTC
strings produced by the caller (`now_utc()` helps).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from .models import PlayerInfo, TournamentMeta


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = resources.files("mr_mouse_stats").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by older schema versions."""
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(settings_observations)")
    }
    additions = {
        "source_message_id": "INTEGER REFERENCES twitch_messages(id)",
        "polling_rate": "INTEGER",
        "zoom_sens": "REAL",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE settings_observations ADD COLUMN {name} {ddl}"
            )


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert_tournament(
        self, liquipedia_page: str, meta: TournamentMeta, fetched_at: str
    ) -> int:
        row = self.conn.execute(
            """
            INSERT INTO tournaments
                (liquipedia_page, name, series, tier, start_date, end_date, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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
            INSERT INTO teams (name) VALUES (?)
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
            VALUES (?, ?, ?, ?)
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
                player_id = ?,
                real_name = ?,
                romanized_name = ?,
                country = ?,
                roles = ?,
                resolution_status = 'resolved',
                updated_at = ?
            WHERE id = ?
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
            "UPDATE players SET resolution_status = ?, updated_at = ? WHERE id = ?",
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                int(is_sub),
                int(is_staff),
                None if played is None else int(played),
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
        cursor = self.conn.execute(
            """
            INSERT INTO social_accounts
                (player_id, platform, handle, url, source, observed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (player_id, platform, handle) DO NOTHING
            """,
            (player_db_id, platform, handle, url, source, observed_at),
        )
        return cursor.rowcount == 1

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
            "source_message_id", "polling_rate", "zoom_sens",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown settings fields: {sorted(unknown)}")
        columns = ["player_id", "observed_at", "source", *fields]
        values = [player_db_id, observed_at, source, *fields.values()]
        placeholders = ", ".join("?" for _ in values)
        cursor = self.conn.execute(
            f"INSERT INTO settings_observations ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
        return cursor.lastrowid

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
        cursor = self.conn.execute(
            """
            INSERT INTO twitch_messages
                (msg_id, observed_at, channel, login, display_name, user_id,
                 badges, kind, trigger_id, text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (msg_id) DO NOTHING
            """,
            (msg_id, observed_at, channel, login, display_name, user_id,
             badges, kind, trigger_id, text),
        )
        return cursor.lastrowid if cursor.rowcount == 1 else None

    def unparsed_response_messages(self) -> list[sqlite3.Row]:
        """Candidate responses with no derived settings observation yet."""
        return self.conn.execute(
            """
            SELECT * FROM twitch_messages tm
            WHERE tm.kind IN ('bot_response', 'broadcaster_response')
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
            "SELECT LOWER(handle) handle, player_id FROM social_accounts "
            "WHERE platform = 'twitch'"
        ).fetchall()
        return {row["handle"]: row["player_id"] for row in rows}

    def resolved_players(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, liquipedia_page FROM players "
            "WHERE resolution_status = 'resolved' ORDER BY liquipedia_page"
        ).fetchall()

    def has_settings_observation(
        self, player_db_id: int, source: str, observed_at: str
    ) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM settings_observations "
            "WHERE player_id = ? AND source = ? AND observed_at = ?",
            (player_db_id, source, observed_at),
        ).fetchone()
        return row is not None

    def commit(self) -> None:
        self.conn.commit()
