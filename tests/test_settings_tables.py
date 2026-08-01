from pathlib import Path

from mr_mouse_stats import cli, db
from mr_mouse_stats.liquipedia.settings_tables import parse_mouse_settings
from mr_mouse_stats.models import WikiPage

FIXTURES = Path(__file__).parent / "fixtures"


def test_table_with_ref_and_windows(fixture_text):
    [entry] = parse_mouse_settings(fixture_text("player_Shpeediry.wikitext"))
    assert entry.date == "2023-03-29"
    assert entry.brand == "Finalmouse"
    assert entry.model == "Starlight Pro TenZ"
    assert entry.dpi == 800
    assert entry.sensitivity == 6.0
    assert entry.windows == 6
    assert entry.polling is None
    assert entry.ref_url == "https://nightbot.tv/t/speedily_/commands"
    assert entry.pad_brand is None  # present but empty in the template


def test_table_with_polling_zoom_and_pad(fixture_text):
    [entry] = parse_mouse_settings(fixture_text("player_Nero.wikitext"))
    assert entry.date == "2020-05-04"
    assert entry.brand == "Logitech"
    assert entry.model == "G PRO X SUPERLIGHT"
    assert entry.polling == 1000
    assert entry.zoom == 30.0
    assert entry.pad_brand == "Logitech"
    assert entry.pad_model == "G640"
    assert entry.ref_url is None


def test_table_with_decimal_sensitivity(fixture_text):
    [entry] = parse_mouse_settings(fixture_text("player_Delenaa.wikitext"))
    assert entry.dpi == 1600
    assert entry.sensitivity == 0.64
    assert entry.polling == 4000


def test_page_without_table(fixture_text):
    assert parse_mouse_settings(fixture_text("player_Energy.wikitext")) == []


def test_multiple_tables_returned_in_order():
    text = (
        "{{Mouse settings table|date=2025-01-01|dpi=800}}\n"
        "{{Mouse settings table|date=2026-01-01|dpi=1600}}\n"
    )
    entries = parse_mouse_settings(text)
    assert [e.date for e in entries] == ["2025-01-01", "2026-01-01"]
    assert [e.dpi for e in entries] == [800, 1600]


def test_garbage_numbers_become_none():
    [entry] = parse_mouse_settings(
        "{{Mouse settings table|date=2026-01-01|dpi=unknown|sensitivity=high}}"
    )
    assert entry.dpi is None
    assert entry.sensitivity is None
    assert entry.date == "2026-01-01"


class TestIngestCommand:
    def seed_db(self, tmp_path):
        path = tmp_path / "t.sqlite3"
        conn = db.connect(path)
        store = db.Store(conn)
        for page in ("Shpeediry", "Nero", "Energy"):
            store.upsert_player_stub(page, "resolved", "t0")
        store.upsert_player_stub("Ghost", "not_player_page", "t0")
        store.commit()
        conn.close()
        return path

    def stub_pages(self, monkeypatch):
        def fetch_pages(client, titles, chunk_size=50):
            out = {}
            for title in titles:
                fixture = FIXTURES / f"player_{title}.wikitext"
                out[title] = WikiPage(title, fixture.read_text())
            return out

        monkeypatch.setattr(cli.api, "fetch_pages", fetch_pages)

    def test_ingest_and_idempotence(self, tmp_path, monkeypatch, capsys):
        path = self.seed_db(tmp_path)
        self.stub_pages(monkeypatch)
        assert cli.main(["ingest-liquipedia-settings", "--db", str(path)]) == 0
        assert "2 settings observations added" in capsys.readouterr().out

        conn = db.connect(path)
        rows = conn.execute(
            "SELECT * FROM settings_observations ORDER BY observed_at"
        ).fetchall()
        assert len(rows) == 2
        nero, shpeediry = rows
        assert nero["source"] == "liquipedia"
        assert nero["polling_rate"] == 1000
        assert nero["zoom_sens"] == 30.0
        assert nero["pad_model"] == "G640"
        assert shpeediry["ref_url"] == "https://nightbot.tv/t/speedily_/commands"
        conn.close()

        assert cli.main(["ingest-liquipedia-settings", "--db", str(path)]) == 0
        assert "0 settings observations added, 2 already present" in capsys.readouterr().out

    def test_ingest_dry_run(self, tmp_path, monkeypatch, capsys):
        path = self.seed_db(tmp_path)
        self.stub_pages(monkeypatch)
        assert cli.main(["ingest-liquipedia-settings", "--db", str(path), "--dry-run"]) == 0
        assert "dry run" in capsys.readouterr().out
        conn = db.connect(path)
        count = conn.execute("SELECT COUNT(*) c FROM settings_observations").fetchone()["c"]
        assert count == 0
