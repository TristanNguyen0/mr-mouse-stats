"""End-to-end CLI test against fixtures — no network, no live API."""

import sqlite3
from pathlib import Path

import pytest

from mr_mouse_stats import cli
from mr_mouse_stats.models import WikiPage

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "MR_Ignite/2026/Mid_Season_Finals"

FIXTURE_PLAYERS = {
    "energy": "player_Energy.wikitext",
    "fate": "player_Fate.wikitext",
    "Jur3ky": "player_Jur3ky.wikitext",
    "Shpeediry": "player_Shpeediry.wikitext",
    "TAROCOOK1E": "player_TAROCOOK1E.wikitext",
    "Ghost": "player_Ghost_disambiguation.wikitext",
}


@pytest.fixture
def stub_api(monkeypatch):
    def fetch_page(client, title):
        assert title == PAGE
        text = (FIXTURES / "MR_Ignite_2026_Mid_Season_Finals.wikitext").read_text()
        return WikiPage(title, text)

    def fetch_pages(client, titles, chunk_size=50):
        result = {}
        for name in titles:
            if name in FIXTURE_PLAYERS:
                canonical = name[:1].upper() + name[1:]
                result[name] = WikiPage(
                    canonical, (FIXTURES / FIXTURE_PLAYERS[name]).read_text()
                )
            else:
                result[name] = WikiPage(name, None, missing=True)
        return result

    monkeypatch.setattr(cli.api, "fetch_page", fetch_page)
    monkeypatch.setattr(cli.api, "fetch_pages", fetch_pages)


def run(tmp_path, *extra):
    db_path = tmp_path / "test.sqlite3"
    code = cli.main(["fetch-roster", PAGE, "--db", str(db_path), *extra])
    return code, db_path


def test_dry_run_writes_nothing(stub_api, tmp_path, capsys):
    code, db_path = run(tmp_path, "--dry-run")
    assert code == 0
    assert not db_path.exists()
    out = capsys.readouterr().out
    assert "Marvel Rivals Ignite 2026: Mid Season Finals" in out
    assert "dry run" in out


def test_persists_roster_and_socials(stub_api, tmp_path, capsys):
    code, db_path = run(tmp_path)
    assert code == 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    assert conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == 10
    n_roster = conn.execute("SELECT COUNT(*) c FROM roster_entries").fetchone()["c"]
    assert n_roster > 60  # players + subs + staff across 10 teams

    energy = conn.execute(
        "SELECT * FROM players WHERE liquipedia_page = 'Energy'"
    ).fetchone()
    assert energy["resolution_status"] == "resolved"
    assert energy["player_id"] == "energy"
    twitch = conn.execute(
        "SELECT handle FROM social_accounts sa JOIN players p ON p.id = sa.player_id "
        "WHERE p.liquipedia_page = 'Energy' AND sa.platform = 'twitch'"
    ).fetchone()
    assert twitch["handle"] == "energy"

    ghost = conn.execute(
        "SELECT resolution_status FROM players WHERE liquipedia_page = 'Ghost'"
    ).fetchone()
    assert ghost["resolution_status"] == "not_player_page"

    staff = conn.execute(
        "SELECT resolution_status FROM players WHERE liquipedia_page = 'LegitRc'"
    ).fetchone()
    assert staff["resolution_status"] == "skipped_staff"
    n_staff_socials = conn.execute(
        "SELECT COUNT(*) c FROM social_accounts sa JOIN players p ON p.id = sa.player_id "
        "WHERE p.resolution_status = 'skipped_staff'"
    ).fetchone()["c"]
    assert n_staff_socials == 0

    missing = conn.execute(
        "SELECT COUNT(*) c FROM players WHERE resolution_status = 'missing'"
    ).fetchone()["c"]
    assert missing > 0  # every non-fixture player is missing in the stub


def test_rerun_is_idempotent(stub_api, tmp_path):
    code1, db_path = run(tmp_path)
    conn = sqlite3.connect(db_path)
    counts1 = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("teams", "players", "roster_entries", "social_accounts")
    }
    conn.close()
    code2, _ = run(tmp_path)
    assert code1 == code2 == 0
    conn = sqlite3.connect(db_path)
    counts2 = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("teams", "players", "roster_entries", "social_accounts")
    }
    assert counts1 == counts2
