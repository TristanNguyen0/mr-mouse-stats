import pytest

from mr_mouse_stats import db
from mr_mouse_stats.models import TournamentMeta
from mr_mouse_stats.site.build import build_site
from mr_mouse_stats.site.svg import bar_chart


@pytest.fixture
def site_db(tmp_path):
    path = tmp_path / "site.sqlite3"
    conn = db.connect(path)
    store = db.Store(conn)
    tid = store.upsert_tournament(
        "T", TournamentMeta("Test Cup", None, None, None, None), "t0"
    )
    team = store.get_or_create_team("Testers")
    covered = store.upsert_player_stub("Team/Alpha <X>", "resolved", "t0")
    conn.execute(
        "UPDATE players SET player_id = 'Alpha', roles = 'Duelist' WHERE id = ?",
        (covered,),
    )
    store.upsert_roster_entry(tid, team, covered, "dps", False, False, None, "Main")
    empty = store.upsert_player_stub("NoData", "resolved", "t0")
    store.upsert_roster_entry(tid, team, empty, "sup", False, False, None, "Main")
    store.add_settings_observation(
        covered, "2026-07-01T10:00:00+00:00", "twitch_chat", channel="alpha",
        raw_text="1600 0.85 & <script>", dpi=1600, sensitivity=0.85,
        mouse_brand="Razer", mouse_model="Viper V3",
    )
    store.commit()
    yield conn
    conn.close()


def test_build_site_writes_expected_pages(site_db, tmp_path):
    out = tmp_path / "site"
    pages = build_site(site_db, out, generated_at="2026-08-01")
    assert pages == 3
    assert (out / "index.html").exists()
    assert (out / "players.html").exists()
    player_pages = list((out / "players").glob("*.html"))
    assert len(player_pages) == 1
    assert player_pages[0].name == "Team_Alpha_X.html"  # unsafe chars slugged


def test_index_content(site_db, tmp_path):
    build_site(site_db, tmp_path, generated_at="2026-08-01")
    page = (tmp_path / "index.html").read_text()
    assert "1 of 2 tracked players" in page
    assert "<svg" in page  # dpi chart inlined, not escaped
    assert "Liquipedia" in page and "CC-BY-SA 3.0" in page


def test_players_table_links_only_covered_players(site_db, tmp_path):
    build_site(site_db, tmp_path, generated_at="2026-08-01")
    page = (tmp_path / "players.html").read_text()
    assert 'href="players/Team_Alpha_X.html"' in page
    assert "NoData" in page
    assert "Team_Alpha_X" in page
    assert page.count("<a href=\"players/") == 1


def test_player_page_escapes_raw_text(site_db, tmp_path):
    build_site(site_db, tmp_path, generated_at="2026-08-01")
    page = (tmp_path / "players" / "Team_Alpha_X.html").read_text()
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "Razer Viper V3" in page
    assert 'href="../index.html"' in page


def test_bar_chart_escapes_labels_and_scales():
    chart = bar_chart([("a<b", 3), ("ok", 1)])
    assert "a&lt;b" in chart
    assert "<svg" in chart
    assert bar_chart([]) == ""
