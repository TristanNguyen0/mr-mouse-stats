"""Public API: read-only, unauthenticated, no writes reachable."""

import pytest
from fastapi.testclient import TestClient

from mr_mouse_stats import db
from mr_mouse_stats.api import deps
from mr_mouse_stats.api.public import create_app
from mr_mouse_stats.models import TournamentMeta


@pytest.fixture
def client(conn):
    store = db.Store(conn)
    tid = store.upsert_tournament(
        "T", TournamentMeta("Test Cup", None, None, None, None), "t0"
    )
    team = store.get_or_create_team("Testers")
    alpha = store.upsert_player_stub("Alpha", "resolved", "t0")
    conn.execute(
        "UPDATE players SET player_id = 'Alpha', roles = 'Duelist', "
        "country = 'Sweden' WHERE id = %s",
        (alpha,),
    )
    store.upsert_roster_entry(tid, team, alpha, "dps", False, False, None, "Main")
    bare = store.upsert_player_stub("Beta", "resolved", "t0")
    conn.execute(
        "UPDATE players SET player_id = 'Beta', roles = 'Vanguard' WHERE id = %s",
        (bare,),
    )
    store.add_settings_observation(
        alpha, "2026-07-01T10:00:00+00:00", "twitch_chat", channel="alpha",
        raw_text="1600 0.85", dpi=1600, sensitivity=0.85,
        mouse_brand="Razer", mouse_model="Viper V3",
    )
    store.add_settings_observation(
        alpha, "2026-07-02T10:00:00+00:00", "twitch_chat", channel="alpha",
        raw_text="1600 0.85", dpi=1600, sensitivity=0.85,
        mouse_brand="Razer", mouse_model="Viper V3",
    )
    store.commit()

    app = create_app()
    app.dependency_overrides[deps.get_conn] = lambda: conn
    with TestClient(app) as c:
        c.player_id = alpha
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "service": "public"}


def test_list_players_includes_uncovered(client):
    rows = client.get("/players").json()
    assert [r["display_name"] for r in rows] == ["Alpha", "Beta"]
    assert rows[0]["edpi"] == pytest.approx(1360.0)
    assert rows[0]["mouse"] == "Razer Viper V3"
    assert rows[1]["observations"] == 0


def test_covered_only_filter(client):
    rows = client.get("/players", params={"covered_only": True}).json()
    assert [r["display_name"] for r in rows] == ["Alpha"]


def test_get_player_and_404(client):
    assert client.get(f"/players/{client.player_id}").json()["display_name"] == "Alpha"
    assert client.get("/players/999999").status_code == 404


def test_history_collapses_identical_readings(client):
    rows = client.get(f"/players/{client.player_id}/history").json()
    assert len(rows) == 1  # two identical observations collapsed into one stint
    assert rows[0]["times_seen"] == 2
    assert rows[0]["first_seen_at"] < rows[0]["last_seen_at"]


def test_stats(client):
    body = client.get("/stats").json()
    assert body["total_players"] == 2
    assert body["covered_players"] == 1
    assert body["dpi_distribution"] == [{"label": "1600", "count": 1}]
    assert body["mouse_popularity"] == [{"label": "Razer Viper V3", "count": 1}]
    assert body["total_teams"] == 1
    assert body["total_observations"] == 2
    assert body["edpi"] == {
        "count": 1, "median": 1360.0, "mean": 1360.0, "low": 1360.0, "high": 1360.0
    }
    assert body["dpi"]["median"] == 1600.0
    roles = {r["role"]: r for r in body["roles"]}
    assert roles["Duelist"]["median_edpi"] == pytest.approx(1360.0)
    assert roles["Vanguard"]["median_edpi"] is None


def test_attribution_is_present(client):
    body = client.get("/attribution").json()
    assert body["license"] == "CC-BY-SA 3.0"
    assert "liquipedia" in body["source_url"]


def test_public_api_exposes_no_write_routes(client):
    """The read/write split is structural: no non-GET route should exist."""
    for route in client.app.routes:
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        assert methods <= {"GET"}, f"{route.path} exposes {methods}"
