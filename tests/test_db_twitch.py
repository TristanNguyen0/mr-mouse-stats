import pytest

from mr_mouse_stats import db


@pytest.fixture
def store():
    conn = db.connect(":memory:")
    yield db.Store(conn)
    conn.close()


def record(store, msg_id="uuid-1", kind="trigger", trigger_id=None, text="!dpi"):
    return store.record_twitch_message(
        msg_id=msg_id, observed_at="2026-08-01T02:00:00+00:00",
        channel="shpeediry", login="viewer1", display_name="Viewer1",
        user_id="123", badges="", kind=kind, text=text, trigger_id=trigger_id,
    )


def test_twitch_message_recorded_and_deduped(store):
    first = record(store)
    assert first is not None
    assert record(store) is None  # same msg_id: reconnect overlap dropped
    count = store.conn.execute("SELECT COUNT(*) c FROM twitch_messages").fetchone()["c"]
    assert count == 1


def test_null_msg_ids_do_not_collide(store):
    assert record(store, msg_id=None) is not None
    assert record(store, msg_id=None) is not None


def test_response_links_to_trigger(store):
    trigger_id = record(store)
    response_id = record(
        store, msg_id="uuid-2", kind="bot_response",
        trigger_id=trigger_id, text="800 dpi 6 sens",
    )
    row = store.conn.execute(
        "SELECT trigger_id FROM twitch_messages WHERE id = ?", (response_id,)
    ).fetchone()
    assert row["trigger_id"] == trigger_id


def test_unparsed_response_messages_excludes_parsed(store):
    pid = store.upsert_player_stub("Shpeediry", "resolved", "t0")
    trigger_id = record(store)
    r1 = record(store, msg_id="uuid-2", kind="bot_response", trigger_id=trigger_id)
    r2 = record(store, msg_id="uuid-3", kind="broadcaster_response", trigger_id=trigger_id)
    assert {row["id"] for row in store.unparsed_response_messages()} == {r1, r2}
    store.add_settings_observation(
        pid, "2026-08-01T02:00:05+00:00", "twitch_chat",
        dpi=800, source_message_id=r1,
    )
    assert {row["id"] for row in store.unparsed_response_messages()} == {r2}
    # triggers are never parse candidates
    assert trigger_id not in {row["id"] for row in store.unparsed_response_messages()}


def test_player_ids_by_twitch_channel(store):
    pid = store.upsert_player_stub("Veswa", "resolved", "t0")
    store.record_social_account(pid, "twitch", "Veswa", None, "t0")
    store.record_social_account(pid, "twitter", "veswa_tw", None, "t0")
    assert store.player_ids_by_twitch_channel() == {"veswa": pid}


def test_migration_adds_source_message_id_to_old_db(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    # simulate a stage-1 database: settings_observations without the column
    old.execute(
        "CREATE TABLE settings_observations ("
        "id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL, "
        "observed_at TEXT NOT NULL, source TEXT NOT NULL)"
    )
    old.commit()
    old.close()
    conn = db.connect(path)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(settings_observations)")}
    assert "source_message_id" in columns
    conn.close()
