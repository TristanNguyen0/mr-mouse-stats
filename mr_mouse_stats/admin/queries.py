"""Read-only queries backing the admin surface.

Extracted from the Flask dashboard so the FastAPI admin app and the
dashboard can share one definition of "what needs attention".
"""

from __future__ import annotations

from .. import db


def counts(c: db.Connection) -> dict[str, int]:
    scalar = lambda sql: c.execute(sql).fetchone()["n"]  # noqa: E731
    return {
        "players": scalar("SELECT COUNT(*) n FROM players"),
        "resolved": scalar(
            "SELECT COUNT(*) n FROM players WHERE resolution_status = 'resolved'"
        ),
        "active_twitch": scalar(
            "SELECT COUNT(*) n FROM social_accounts "
            "WHERE platform = 'twitch' AND retired_at IS NULL"
        ),
        "failing_channels": scalar(
            "SELECT COUNT(*) n FROM channel_join_status WHERE NOT confirmed"
        ),
        "players_without_twitch": len(players_without_twitch(c)),
        "unresolved": len(unresolved_players(c)),
        "candidates": len(unparsed_candidates(c)),
    }


def observations_by_source(c: db.Connection) -> list[db.Row]:
    return c.execute(
        "SELECT source, COUNT(*) n FROM settings_observations "
        "GROUP BY source ORDER BY source"
    ).fetchall()


def messages_by_kind(c: db.Connection) -> list[db.Row]:
    return c.execute(
        "SELECT kind, COUNT(*) n FROM twitch_messages GROUP BY kind ORDER BY kind"
    ).fetchall()


def failing_channels(c: db.Connection) -> list[db.Row]:
    return c.execute(
        """
        SELECT cjs.channel, cjs.last_checked_at,
               sa.id AS account_id, sa.handle,
               p.id AS player_id, p.liquipedia_page, p.real_name,
               (SELECT string_agg(platform || ':' || handle, '  '
                                  ORDER BY platform, handle)
                FROM social_accounts o
                WHERE o.player_id = p.id AND o.platform != 'twitch'
                  AND o.retired_at IS NULL) AS other_socials
        FROM channel_join_status cjs
        JOIN social_accounts sa
          ON LOWER(sa.handle) = cjs.channel
         AND sa.platform = 'twitch' AND sa.retired_at IS NULL
        JOIN players p ON p.id = sa.player_id
        WHERE NOT cjs.confirmed
        ORDER BY cjs.channel
        """
    ).fetchall()


def players_without_twitch(c: db.Connection) -> list[db.Row]:
    return c.execute(
        """
        SELECT p.id AS player_id, p.liquipedia_page, p.player_id AS handle_name,
               p.real_name, p.country,
               (SELECT string_agg(platform || ':' || handle, '  '
                                  ORDER BY platform, handle)
                FROM social_accounts o
                WHERE o.player_id = p.id AND o.retired_at IS NULL) AS other_socials
        FROM players p
        WHERE p.resolution_status = 'resolved'
          AND NOT EXISTS (
              SELECT 1 FROM social_accounts sa
              WHERE sa.player_id = p.id AND sa.platform = 'twitch'
                AND sa.retired_at IS NULL
          )
        ORDER BY p.liquipedia_page COLLATE "C"
        """
    ).fetchall()


def unresolved_players(c: db.Connection) -> list[db.Row]:
    return c.execute(
        """
        SELECT p.*, t.name AS team_name
        FROM players p
        LEFT JOIN roster_entries re ON re.player_id = p.id
        LEFT JOIN teams t ON t.id = re.team_id
        WHERE p.resolution_status NOT IN ('resolved', 'skipped_staff')
        ORDER BY p.liquipedia_page COLLATE "C"
        """
    ).fetchall()


def unparsed_candidates(c: db.Connection) -> list[db.Row]:
    return c.execute(
        """
        SELECT tm.*, trig.text AS trigger_text, trig.login AS trigger_login
        FROM twitch_messages tm
        LEFT JOIN twitch_messages trig ON trig.id = tm.trigger_id
        WHERE tm.kind IN ('bot_response', 'broadcaster_response')
          AND tm.dismissed_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM settings_observations so
              WHERE so.source_message_id = tm.id
          )
        ORDER BY tm.observed_at DESC
        """
    ).fetchall()


def message_by_id(c: db.Connection, message_id: int) -> db.Row | None:
    return c.execute(
        "SELECT * FROM twitch_messages WHERE id = %s", (message_id,)
    ).fetchone()
