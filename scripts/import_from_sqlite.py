"""One-shot import of the legacy SQLite database into Postgres.

Preserves primary keys, because settings_observations.source_message_id and
twitch_messages.trigger_id reference them. Identity sequences are advanced
past the imported maximum afterwards so later inserts don't collide.

Re-runnable: it refuses to touch a non-empty target unless --truncate is
given, and the source file is never modified.

    uv run python scripts/import_from_sqlite.py \
        --sqlite data/mr_mouse_stats.sqlite3 \
        --db postgresql://postgres:postgres@localhost:55432/mr_mouse_stats
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from mr_mouse_stats import config, db

# Parents before children; also the order TRUNCATE unwinds in reverse.
TABLES = (
    "tournaments",
    "teams",
    "players",
    "roster_entries",
    "social_accounts",
    "twitch_messages",
    "settings_observations",
    "channel_join_status",
)

# SQLite stores these as 0/1 (or NULL for the tri-state `played`).
BOOL_COLUMNS = {
    ("roster_entries", "is_sub"),
    ("roster_entries", "is_staff"),
    ("roster_entries", "played"),
    ("channel_join_status", "confirmed"),
}

# Everything except channel_join_status, which is keyed by channel name.
IDENTITY_TABLES = tuple(t for t in TABLES if t != "channel_join_status")


def _columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in src.execute(f"PRAGMA table_info({table})")]


def _convert(table: str, column: str, value):
    if value is not None and (table, column) in BOOL_COLUMNS:
        return bool(value)
    return value


def import_all(sqlite_path: str, dsn: str, truncate: bool) -> dict[str, int]:
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dest = db.connect(dsn)

    existing = {
        table: dest.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        for table in TABLES
    }
    non_empty = {t: n for t, n in existing.items() if n}
    if non_empty and not truncate:
        raise SystemExit(
            f"target already has rows in {sorted(non_empty)}; "
            "pass --truncate to replace them"
        )
    if truncate:
        dest.execute(
            "TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"
        )

    counts: dict[str, int] = {}
    for table in TABLES:
        columns = _columns(src, table)
        rows = src.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        if rows:
            # OVERRIDING SYSTEM VALUE: keep the source ids on GENERATED ALWAYS.
            placeholders = ", ".join(["%s"] * len(columns))
            statement = (
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"OVERRIDING SYSTEM VALUE VALUES ({placeholders})"
            )
            with dest.cursor() as cur:
                cur.executemany(
                    statement,
                    [
                        [_convert(table, c, row[c]) for c in columns]
                        for row in rows
                    ],
                )
        counts[table] = len(rows)

    for table in IDENTITY_TABLES:
        dest.execute(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
            (table,),
        )

    dest.commit()
    dest.close()
    src.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default="data/mr_mouse_stats.sqlite3")
    parser.add_argument("--db", default=config.db(), help="target postgres DSN")
    parser.add_argument(
        "--truncate", action="store_true", help="replace existing target rows"
    )
    args = parser.parse_args()

    counts = import_all(args.sqlite, args.db, args.truncate)
    width = max(len(t) for t in counts)
    for table, n in counts.items():
        print(f"  {table:<{width}}  {n}")
    print(f"imported {sum(counts.values())} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
