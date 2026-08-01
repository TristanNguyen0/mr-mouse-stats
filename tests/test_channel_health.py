import pytest

from mr_mouse_stats import db
from mr_mouse_stats.twitch.runner import collect


@pytest.fixture
def store():
    conn = db.connect(":memory:")
    yield db.Store(conn)
    conn.close()


class QuietClient:
    """Yields only heartbeats; scripted clock drives the join report."""

    def __init__(self, channels, confirmed):
        self.channels = channels
        self.confirmed_joins = set(confirmed)
        self.unconfirmed_joins = set(channels) - self.confirmed_joins

    def run(self):
        while True:
            yield None


def test_collect_persists_join_status(store):
    client = QuietClient(["alpha", "beta", "gone"], confirmed=["alpha", "beta"])
    clock_values = iter([0.0, 61.0, 61.0, 200.0])
    collect(client, store, duration=100.0, clock=lambda: next(clock_values))
    rows = {
        r["channel"]: r["confirmed"]
        for r in store.conn.execute("SELECT * FROM channel_join_status")
    }
    assert rows == {"alpha": 1, "beta": 1, "gone": 0}


def test_join_status_upsert_updates_on_recheck(store):
    store.upsert_channel_join_status("gone", False, "t0")
    store.upsert_channel_join_status("GONE", True, "t1")  # handle fixed
    rows = store.conn.execute("SELECT * FROM channel_join_status").fetchall()
    assert len(rows) == 1
    assert rows[0]["confirmed"] == 1
    assert rows[0]["last_checked_at"] == "t1"


def test_retired_handles_excluded_from_channel_map(store):
    pid = store.upsert_player_stub("Alx", "resolved", "t0")
    store.record_social_account(pid, "twitch", "realalex", None, "t0")
    assert store.player_ids_by_twitch_channel() == {"realalex": pid}

    account_id = store.conn.execute(
        "SELECT id FROM social_accounts WHERE handle = 'realalex'"
    ).fetchone()["id"]
    store.retire_social_account(account_id, "t5")
    store.record_social_account(pid, "twitch", "alx_new", None, "t5")
    assert store.player_ids_by_twitch_channel() == {"alx_new": pid}

    # the retired row is preserved as history, not deleted
    row = store.conn.execute(
        "SELECT retired_at FROM social_accounts WHERE handle = 'realalex'"
    ).fetchone()
    assert row["retired_at"] == "t5"


def test_retire_does_not_overwrite_existing_retirement(store):
    pid = store.upsert_player_stub("X", "resolved", "t0")
    store.record_social_account(pid, "twitch", "old", None, "t0")
    account_id = store.conn.execute("SELECT id FROM social_accounts").fetchone()["id"]
    store.retire_social_account(account_id, "t1")
    store.retire_social_account(account_id, "t9")
    row = store.conn.execute("SELECT retired_at FROM social_accounts").fetchone()
    assert row["retired_at"] == "t1"


def test_migration_adds_retired_at_to_old_db(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE social_accounts ("
        "id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL, "
        "platform TEXT NOT NULL, handle TEXT NOT NULL, url TEXT, "
        "source TEXT NOT NULL DEFAULT 'liquipedia', observed_at TEXT NOT NULL)"
    )
    old.commit()
    old.close()
    conn = db.connect(path)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(social_accounts)")}
    assert "retired_at" in columns
    conn.close()
