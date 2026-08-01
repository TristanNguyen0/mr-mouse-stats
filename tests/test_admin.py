import pytest

from mr_mouse_stats import db
from mr_mouse_stats.admin.app import create_app
from mr_mouse_stats.models import TournamentMeta


@pytest.fixture
def app_db(tmp_path):
    path = tmp_path / "admin.sqlite3"
    conn = db.connect(path)
    store = db.Store(conn)

    tid = store.upsert_tournament(
        "T", TournamentMeta("Test Cup", None, None, None, None), "t0"
    )
    team = store.get_or_create_team("Testers")

    good = store.upsert_player_stub("GoodPlayer", "resolved", "t0")
    store.record_social_account(good, "twitch", "goodplayer", None, "t0")
    stale = store.upsert_player_stub("StalePlayer", "resolved", "t0")
    store.record_social_account(stale, "twitch", "oldhandle", None, "t0")
    store.record_social_account(stale, "twitter", "stale_tw", None, "t0")
    no_twitch = store.upsert_player_stub("NoTwitch", "resolved", "t0")
    store.record_social_account(no_twitch, "bilibili", "12345", None, "t0")
    ghost = store.upsert_player_stub("GhostPage", "not_player_page", "t0")
    store.upsert_roster_entry(tid, team, ghost, "sup", False, False, None, "Main")

    store.upsert_channel_join_status("oldhandle", False, "t1")
    store.upsert_channel_join_status("goodplayer", True, "t1")

    trig = store.record_twitch_message(
        "m-1", "2026-08-01T00:00:00+00:00", "goodplayer", "viewer",
        "Viewer", "1", "", "trigger", "!dpi",
    )
    store.record_twitch_message(
        "m-2", "2026-08-01T00:00:05+00:00", "goodplayer", "nightbot",
        "Nightbot", "2", "bot-badge/1", "bot_response",
        "some unparseable answer", trigger_id=trig,
    )
    store.commit()
    conn.close()
    return path


@pytest.fixture
def client(app_db):
    app = create_app(str(app_db))
    app.config["TESTING"] = True
    return app.test_client()


def test_overview(client):
    page = client.get("/").get_data(as_text=True)
    assert "4</b> players" in page
    assert "1</b> channels failing to join" in page
    assert "1</b> players without twitch" in page
    assert "1</b> unresolved players" in page


def test_handles_page_lists_problems(client):
    page = client.get("/handles").get_data(as_text=True)
    assert "oldhandle" in page
    assert "StalePlayer" in page
    assert "twitter:stale_tw" in page  # context to hunt the new handle
    assert "NoTwitch" in page
    assert "bilibili:12345" in page
    assert "goodplayer" not in page  # healthy channel not listed


def test_unresolved_page(client):
    page = client.get("/unresolved").get_data(as_text=True)
    assert "GhostPage" in page
    assert "not_player_page" in page
    assert "Testers" in page


def test_candidates_page(client):
    page = client.get("/candidates").get_data(as_text=True)
    assert "some unparseable answer" in page
    assert "re: !dpi" in page


def test_replace_handle_appends_and_retires(client, app_db):
    conn = db.connect(app_db)
    account_id, player_id = conn.execute(
        "SELECT id, player_id FROM social_accounts WHERE handle = 'oldhandle'"
    ).fetchone()
    conn.close()
    resp = client.post(
        "/actions/replace-handle",
        data={"player_id": player_id, "old_account_id": account_id,
              "new_handle": "@NewHandle"},
    )
    assert resp.status_code == 302
    conn = db.connect(app_db)
    rows = conn.execute(
        "SELECT handle, retired_at, source FROM social_accounts "
        "WHERE player_id = ? AND platform = 'twitch' ORDER BY id", (player_id,),
    ).fetchall()
    assert rows[0]["handle"] == "oldhandle"
    assert rows[0]["retired_at"] is not None
    assert rows[1]["handle"] == "NewHandle"  # @ stripped
    assert rows[1]["source"] == "manual"
    assert rows[1]["retired_at"] is None
    # fixed channel no longer listed as failing
    page = client.get("/handles").get_data(as_text=True)
    assert "oldhandle" not in page


def test_add_handle_for_player_without_twitch(client, app_db):
    conn = db.connect(app_db)
    player_id = conn.execute(
        "SELECT id FROM players WHERE liquipedia_page = 'NoTwitch'"
    ).fetchone()["id"]
    conn.close()
    client.post("/actions/replace-handle",
                data={"player_id": player_id, "new_handle": "freshhandle"})
    page = client.get("/handles").get_data(as_text=True)
    assert "NoTwitch" not in page


def test_manual_observation_from_candidate(client, app_db):
    conn = db.connect(app_db)
    message_id = conn.execute(
        "SELECT id FROM twitch_messages WHERE kind = 'bot_response'"
    ).fetchone()["id"]
    conn.close()
    client.post(f"/actions/candidates/{message_id}/manual",
                data={"dpi": "800", "sensitivity": "6", "mouse_brand": "Lamzu",
                      "mouse_model": "Maya"})
    conn = db.connect(app_db)
    row = conn.execute(
        "SELECT * FROM settings_observations WHERE source = 'manual'"
    ).fetchone()
    assert row["dpi"] == 800
    assert row["source_message_id"] == message_id
    assert row["raw_text"] == "some unparseable answer"
    conn.close()
    assert "some unparseable answer" not in client.get("/candidates").get_data(as_text=True)


def test_manual_observation_with_no_values_records_nothing(client, app_db):
    conn = db.connect(app_db)
    message_id = conn.execute(
        "SELECT id FROM twitch_messages WHERE kind = 'bot_response'"
    ).fetchone()["id"]
    conn.close()
    client.post(f"/actions/candidates/{message_id}/manual", data={})
    conn = db.connect(app_db)
    assert conn.execute(
        "SELECT COUNT(*) c FROM settings_observations"
    ).fetchone()["c"] == 0


def test_dismiss_candidate(client, app_db):
    conn = db.connect(app_db)
    message_id = conn.execute(
        "SELECT id FROM twitch_messages WHERE kind = 'bot_response'"
    ).fetchone()["id"]
    conn.close()
    client.post(f"/actions/candidates/{message_id}/dismiss")
    conn = db.connect(app_db)
    row = conn.execute(
        "SELECT dismissed_at FROM twitch_messages WHERE id = ?", (message_id,)
    ).fetchone()
    assert row["dismissed_at"] is not None  # kept, not deleted
    conn.close()
    page = client.get("/candidates").get_data(as_text=True)
    assert "some unparseable answer" not in page
    # dismissed candidates are also excluded from the parse pass
    store = db.Store(db.connect(app_db))
    assert store.unparsed_response_messages() == []
