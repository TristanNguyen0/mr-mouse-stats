import pytest

from mr_mouse_stats import db
from mr_mouse_stats.models import TournamentMeta
from mr_mouse_stats.site import queries


@pytest.fixture
def conn(conn):  # overrides the empty connection from conftest with a seeded one
    connection = conn
    store = db.Store(connection)

    tid = store.upsert_tournament(
        "T", TournamentMeta("Test Cup", None, None, None, None), "t0"
    )
    team = store.get_or_create_team("Testers")

    def player(page, roles, role):
        pid = store.upsert_player_stub(page, "resolved", "t0")
        connection.execute(
            "UPDATE players SET player_id = %s, roles = %s, country = 'Sweden' "
            "WHERE id = %s",
            (page.rsplit("/", 1)[-1], roles, pid),
        )
        store.upsert_roster_entry(tid, team, pid, role, False, False, None, "Main")
        return pid

    duelist = player("Alpha", "Duelist", "dps")
    vanguard = player("Team/Beta", "Vanguard, Coach", "tank")
    bare = player("Gamma", "Strategist", "sup")
    staff = store.upsert_player_stub("Coachy", "skipped_staff", "t0")
    store.upsert_roster_entry(tid, team, staff, "coach", False, True, None, "Main")

    # duelist: settings change over time, repeated readings, liquipedia + twitch
    store.add_settings_observation(
        duelist, "2025-01-01", "liquipedia",
        dpi=800, sensitivity=2.0, mouse_brand="Razer", mouse_model="Viper V3",
    )
    for ts in ("2026-07-01T10:00:00+00:00", "2026-07-02T10:00:00+00:00"):
        store.add_settings_observation(
            duelist, ts, "twitch_chat", channel="alpha",
            raw_text="1600 0.85", dpi=1600, sensitivity=0.85,
        )
    store.add_settings_observation(
        duelist, "2026-07-03T10:00:00+00:00", "twitch_chat", channel="alpha",
        raw_text="Logitech superlight atm", mouse_brand="Logitech",
        mouse_model="G Pro X Superlight",
    )
    # vanguard: single observation with dpi only
    store.add_settings_observation(
        vanguard, "2026-07-01T09:00:00+00:00", "twitch_chat", channel="beta",
        raw_text="800", dpi=800,
    )
    store.commit()
    return connection


def test_summaries_cover_all_resolved_players(conn):
    summaries = queries.player_summaries(conn)
    assert [s.display_name for s in summaries] == ["Alpha", "Gamma", "Beta"]
    assert all(s.team == "Testers" for s in summaries)


def test_summary_latest_per_field_and_edpi(conn):
    alpha = queries.player_summaries(conn)[0]
    assert alpha.dpi == 1600
    assert alpha.sensitivity == 0.85
    # eDPI from the latest observation carrying both fields, not the
    # mouse-only one that came after
    assert alpha.edpi == 1360.0
    assert alpha.mouse == "Logitech G Pro X Superlight"
    assert alpha.observations == 4
    assert alpha.last_observed_at == "2026-07-03T10:00:00+00:00"
    assert alpha.role == "Duelist"


def test_summary_partial_data(conn):
    beta = [s for s in queries.player_summaries(conn) if s.display_name == "Beta"][0]
    assert beta.dpi == 800
    assert beta.sensitivity is None
    assert beta.edpi is None
    assert beta.role == "Vanguard"  # primary role, Coach dropped
    gamma = [s for s in queries.player_summaries(conn) if s.display_name == "Gamma"][0]
    assert gamma.observations == 0
    assert gamma.last_observed_at is None


def test_history_collapses_consecutive_identical_readings(conn):
    alpha_id = [s for s in queries.player_summaries(conn) if s.display_name == "Alpha"][0].db_id
    history = queries.player_history(conn, alpha_id)
    assert [e.times_seen for e in history] == [1, 2, 1]
    stint = history[1]
    assert (stint.dpi, stint.sensitivity) == (1600, 0.85)
    assert stint.first_seen_at == "2026-07-01T10:00:00+00:00"
    assert stint.last_seen_at == "2026-07-02T10:00:00+00:00"
    assert history[0].source == "liquipedia"


def test_distributions(conn):
    summaries = queries.player_summaries(conn)
    assert queries.dpi_distribution(summaries) == [("800", 1), ("1600", 1)]
    assert queries.edpi_distribution(summaries) == [("1200–1400", 1)]
    assert queries.mouse_popularity(summaries) == [("Logitech G Pro X Superlight", 1)]


def test_mouse_popularity_counts_devices_not_spellings(conn, store):
    """Two players naming the same mouse differently are one device, two users."""
    other = store.upsert_player_stub("Delta", "resolved", "t0")
    conn.execute("UPDATE players SET player_id = 'Delta' WHERE id = %s", (other,))
    store.add_settings_observation(
        other, "2026-07-04T10:00:00+00:00", "twitch_chat", channel="delta",
        raw_text="gpx superlight", mouse_model="gpx superlight",
    )
    store.commit()

    summaries = queries.player_summaries(conn)
    assert queries.mouse_popularity(summaries) == [("Logitech G Pro X Superlight", 2)]
    # the player keeps the words they actually used
    delta = next(s for s in summaries if s.display_name == "Delta")
    assert (delta.mouse, delta.device) == (
        "gpx superlight",
        "Logitech G Pro X Superlight",
    )


def test_metrics(conn):
    summaries = queries.player_summaries(conn)
    edpi = queries.edpi_metric(summaries)
    assert (edpi.count, edpi.median, edpi.mean) == (1, 1360.0, 1360.0)
    dpi = queries.dpi_metric(summaries)
    assert (dpi.count, dpi.median, dpi.low, dpi.high) == (2, 1200.0, 800.0, 1600.0)


def test_metrics_of_nothing(conn):
    empty = queries.edpi_metric([])
    assert (empty.count, empty.median, empty.mean) == (0, None, None)


def test_role_comparison(conn):
    rows = queries.role_comparison(queries.player_summaries(conn))
    assert ("Duelist", 1, 1360.0) in rows
    assert ("Vanguard", 0, None) in rows  # dpi-only player has no eDPI
    assert ("Strategist", 0, None) in rows
