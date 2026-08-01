import pytest

from mr_mouse_stats import db
from mr_mouse_stats.models import PlayerInfo, TournamentMeta

META = TournamentMeta(
    name="Mid Season Finals",
    series="Marvel Rivals Ignite",
    tier="1",
    start_date="2026-07-29",
    end_date="2026-08-01",
)


@pytest.fixture
def store():
    conn = db.connect(":memory:")
    yield db.Store(conn)
    conn.close()


def test_tournament_upsert_is_idempotent(store):
    t1 = store.upsert_tournament("MR_Ignite/2026/Mid_Season_Finals", META, "t0")
    t2 = store.upsert_tournament("MR_Ignite/2026/Mid_Season_Finals", META, "t1")
    assert t1 == t2
    row = store.conn.execute("SELECT * FROM tournaments").fetchone()
    assert row["fetched_at"] == "t1"
    assert store.conn.execute("SELECT COUNT(*) c FROM tournaments").fetchone()["c"] == 1


def test_roster_entry_upsert_no_duplicates(store):
    tid = store.upsert_tournament("T", META, "t0")
    team = store.get_or_create_team("Liquid Citadel")
    player = store.upsert_player_stub("Energy", "unresolved", "t0")
    store.upsert_roster_entry(tid, team, player, "dps", False, False, None, "Main")
    store.upsert_roster_entry(tid, team, player, "dps", True, False, None, "Main")
    rows = store.conn.execute("SELECT * FROM roster_entries").fetchall()
    assert len(rows) == 1
    assert rows[0]["is_sub"] == 1  # second write updated in place


def test_player_stub_then_resolution(store):
    pid = store.upsert_player_stub("Energy", "unresolved", "t0")
    again = store.upsert_player_stub("Energy", "unresolved", "t1")
    assert pid == again
    info = PlayerInfo(
        page_title="Energy",
        player_id="energy",
        real_name="Jovan",
        country="United States",
        roles="Duelist",
    )
    store.update_player_resolved(pid, info, "t2")
    row = store.conn.execute("SELECT * FROM players").fetchone()
    assert row["resolution_status"] == "resolved"
    assert row["player_id"] == "energy"
    assert row["first_seen_at"] == "t0"
    assert row["updated_at"] == "t2"


def test_social_accounts_append_only(store):
    pid = store.upsert_player_stub("Energy", "resolved", "t0")
    assert store.record_social_account(pid, "twitch", "energy", "u1", "t0") is True
    # same handle again: no new row, first observation timestamp preserved
    assert store.record_social_account(pid, "twitch", "energy", "u1", "t5") is False
    # handle change appends instead of overwriting
    assert store.record_social_account(pid, "twitch", "energy_ttv", "u2", "t9") is True
    rows = store.conn.execute(
        "SELECT handle, observed_at FROM social_accounts ORDER BY id"
    ).fetchall()
    assert [(r["handle"], r["observed_at"]) for r in rows] == [
        ("energy", "t0"),
        ("energy_ttv", "t9"),
    ]


def test_settings_observations_append(store):
    pid = store.upsert_player_stub("Shpeediry", "resolved", "t0")
    store.add_settings_observation(
        pid, "2023-03-29", "liquipedia",
        dpi=800, sensitivity=6, mouse_brand="Finalmouse",
        mouse_model="Starlight Pro TenZ",
    )
    store.add_settings_observation(
        pid, "2026-08-01", "twitch_chat",
        dpi=1600, sensitivity=3, channel="shpeediry",
        raw_text="@viewer 1600 dpi 3 sens",
    )
    rows = store.conn.execute(
        "SELECT * FROM settings_observations ORDER BY observed_at"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["dpi"] == 800
    assert rows[1]["source"] == "twitch_chat"


def test_settings_observation_rejects_unknown_field(store):
    pid = store.upsert_player_stub("X", "resolved", "t0")
    with pytest.raises(ValueError, match="unknown settings fields"):
        store.add_settings_observation(pid, "t1", "manual", edpi=4800)


def test_foreign_keys_enforced(store):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.record_social_account(999, "twitch", "ghost", None, "t0")
