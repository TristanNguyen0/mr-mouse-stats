"""Localhost admin dashboard: surface missing/suspect data, apply
append-only manual fixes. No auth — bind to localhost only."""

from __future__ import annotations

import os
import sqlite3

from flask import Flask, g, render_template

from .. import db


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    app.secret_key = os.urandom(16)  # only used for flash messages

    def conn() -> sqlite3.Connection:
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    app.get_conn = conn  # used by the action blueprint

    @app.teardown_appcontext
    def close_conn(_exc):
        connection = g.pop("conn", None)
        if connection is not None:
            connection.close()

    @app.route("/")
    def index():
        c = conn()
        counts = {
            "players": c.execute("SELECT COUNT(*) n FROM players").fetchone()["n"],
            "resolved": c.execute(
                "SELECT COUNT(*) n FROM players WHERE resolution_status = 'resolved'"
            ).fetchone()["n"],
            "active_twitch": c.execute(
                "SELECT COUNT(*) n FROM social_accounts "
                "WHERE platform = 'twitch' AND retired_at IS NULL"
            ).fetchone()["n"],
            "failing_channels": c.execute(
                "SELECT COUNT(*) n FROM channel_join_status WHERE confirmed = 0"
            ).fetchone()["n"],
            "players_without_twitch": len(_players_without_twitch(c)),
            "unresolved": len(_unresolved_players(c)),
            "candidates": len(_unparsed_candidates(c)),
        }
        observations = c.execute(
            "SELECT source, COUNT(*) n FROM settings_observations GROUP BY source"
        ).fetchall()
        messages = c.execute(
            "SELECT kind, COUNT(*) n FROM twitch_messages GROUP BY kind"
        ).fetchall()
        return render_template(
            "index.html", counts=counts, observations=observations, messages=messages
        )

    @app.route("/handles")
    def handles():
        c = conn()
        return render_template(
            "handles.html",
            failing=_failing_channels(c),
            missing=_players_without_twitch(c),
        )

    @app.route("/unresolved")
    def unresolved():
        return render_template("unresolved.html", players=_unresolved_players(conn()))

    @app.route("/candidates")
    def candidates():
        return render_template("candidates.html", rows=_unparsed_candidates(conn()))

    from .actions import bp as actions_bp

    app.register_blueprint(actions_bp)
    return app


def _failing_channels(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute(
        """
        SELECT cjs.channel, cjs.last_checked_at,
               sa.id AS account_id, sa.handle,
               p.id AS player_id, p.liquipedia_page, p.real_name,
               (SELECT GROUP_CONCAT(platform || ':' || handle, '  ')
                FROM social_accounts o
                WHERE o.player_id = p.id AND o.platform != 'twitch'
                  AND o.retired_at IS NULL) AS other_socials
        FROM channel_join_status cjs
        JOIN social_accounts sa
          ON LOWER(sa.handle) = cjs.channel
         AND sa.platform = 'twitch' AND sa.retired_at IS NULL
        JOIN players p ON p.id = sa.player_id
        WHERE cjs.confirmed = 0
        ORDER BY cjs.channel
        """
    ).fetchall()


def _players_without_twitch(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute(
        """
        SELECT p.id AS player_id, p.liquipedia_page, p.player_id AS handle_name,
               p.real_name, p.country,
               (SELECT GROUP_CONCAT(platform || ':' || handle, '  ')
                FROM social_accounts o
                WHERE o.player_id = p.id AND o.retired_at IS NULL) AS other_socials
        FROM players p
        WHERE p.resolution_status = 'resolved'
          AND NOT EXISTS (
              SELECT 1 FROM social_accounts sa
              WHERE sa.player_id = p.id AND sa.platform = 'twitch'
                AND sa.retired_at IS NULL
          )
        ORDER BY p.liquipedia_page
        """
    ).fetchall()


def _unresolved_players(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute(
        """
        SELECT p.*, t.name AS team_name
        FROM players p
        LEFT JOIN roster_entries re ON re.player_id = p.id
        LEFT JOIN teams t ON t.id = re.team_id
        WHERE p.resolution_status NOT IN ('resolved', 'skipped_staff')
        ORDER BY p.liquipedia_page
        """
    ).fetchall()


def _unparsed_candidates(c: sqlite3.Connection) -> list[sqlite3.Row]:
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
