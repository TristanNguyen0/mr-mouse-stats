"""fetch-nightbot -> bot_commands -> parse-bot-commands -> observations."""

import argparse
import json
from pathlib import Path

import pytest

from mr_mouse_stats import cli, db
from mr_mouse_stats.http import HttpNotFound, NightbotClient

FIXTURES = Path(__file__).parent / "fixtures"
CHANNEL_ID = "65ce2bff709e0dbaf5f33285"


@pytest.fixture
def player(store):
    """A resolved player whose Twitch handle is the fixture channel."""
    now = db.now_utc()
    player_id = store.upsert_player_stub("Aplycs", "unresolved", now)
    store.record_social_account(player_id, "twitch", "aplycs", None, now)
    store.commit()
    return player_id


@pytest.fixture
def fake_nightbot(monkeypatch):
    """Serve the saved captures instead of the network."""
    channel = json.loads((FIXTURES / "nightbot_channel_aplycs.json").read_text())
    commands = json.loads((FIXTURES / "nightbot_commands_aplycs.json").read_text())
    known = {"aplycs"}

    def transport(url, headers):
        if url.endswith("/commands"):
            return json.dumps(commands).encode()
        if url.rsplit("/", 1)[-1] not in known:
            raise HttpNotFound(url)
        return json.dumps(channel).encode()

    original = NightbotClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs["sleep"] = lambda _: None
        original(self, *args, **kwargs)

    monkeypatch.setattr(NightbotClient, "__init__", patched)
    return known


def fetch_args(dsn, tmp_path, **overrides):
    return argparse.Namespace(
        db=dsn, cache_dir=tmp_path, refresh_cache=False,
        channels=None, dry_run=False, **overrides
    )


def parse_args(dsn, **overrides):
    return argparse.Namespace(db=dsn, dry_run=False, **overrides)


def test_fetch_stores_only_settings_commands(dsn, conn, store, player,
                                             fake_nightbot, tmp_path):
    assert cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path)) == 0
    rows = conn.execute("SELECT * FROM bot_commands ORDER BY name").fetchall()
    assert [r["name"] for r in rows] == ["mouse", "mousepad", "sens"]
    assert all(r["bot"] == "nightbot" for r in rows)
    # !monitor answers "ZOWIE XL2566X+", a brand the mouse parser knows.
    # It must never have been stored in the first place.
    assert not conn.execute(
        "SELECT 1 FROM bot_commands WHERE name = 'monitor'"
    ).fetchone()


def test_channel_status_records_a_channel_with_no_nightbot(dsn, conn, store,
                                                           fake_nightbot, tmp_path):
    args = fetch_args(dsn, tmp_path, )
    args.channels = "shroud"
    assert cli.cmd_fetch_nightbot(args) == 0
    row = conn.execute(
        "SELECT * FROM bot_channel_status WHERE channel = 'shroud'"
    ).fetchone()
    assert row["registered"] is False
    assert row["commands_seen"] == 0


def test_refetching_unchanged_commands_adds_no_rows(dsn, conn, store, player,
                                                    fake_nightbot, tmp_path):
    cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path))
    before = conn.execute("SELECT count(*) AS n FROM bot_commands").fetchone()["n"]
    cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path))
    after = conn.execute("SELECT count(*) AS n FROM bot_commands").fetchone()["n"]
    assert before == after == 3


def test_an_edited_command_appends_a_new_version(dsn, conn, store, player,
                                                 fake_nightbot, tmp_path):
    cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path))
    store.record_bot_command(
        "nightbot", "aplycs", "6641d673ecba39b329068136", "sens",
        "0.25 800dpi", "2026-09-01T10:00:00+00:00", db.now_utc(),
    )
    store.commit()
    versions = conn.execute(
        "SELECT message FROM bot_commands WHERE name = 'sens' ORDER BY updated_at"
    ).fetchall()
    assert [v["message"] for v in versions] == ["0.125 1600dpi", "0.25 800dpi"]


def test_a_bot_with_no_timestamps_dedupes_on_the_text(conn, store):
    """Nightbot always dates its commands; Fossabot and Moobot do not, and
    for those the text is the only version key there is."""
    for _ in range(2):
        store.record_bot_command(
            "fossabot", "aplycs", "322463", "mouse", "Viper V4 PRO", None,
            db.now_utc(),
        )
    store.record_bot_command(
        "fossabot", "aplycs", "322463", "mouse", "Lamzu Maya", None, db.now_utc(),
    )
    store.commit()
    rows = conn.execute(
        "SELECT message FROM bot_commands WHERE bot = 'fossabot'"
    ).fetchall()
    assert sorted(r["message"] for r in rows) == ["Lamzu Maya", "Viper V4 PRO"]


def test_dry_run_writes_nothing(dsn, conn, store, player, fake_nightbot, tmp_path):
    args = fetch_args(dsn, tmp_path)
    args.dry_run = True
    assert cli.cmd_fetch_nightbot(args) == 0
    assert conn.execute("SELECT count(*) AS n FROM bot_commands").fetchone()["n"] == 0


def test_parse_derives_observations_dated_by_the_bots_own_edit_time(
    dsn, conn, store, player, fake_nightbot, tmp_path
):
    cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path))
    assert cli.cmd_parse_bot_commands(parse_args(dsn)) == 0
    rows = conn.execute(
        "SELECT * FROM settings_observations ORDER BY observed_at"
    ).fetchall()
    assert {r["source"] for r in rows} == {"nightbot"}
    by_text = {r["raw_text"]: r for r in rows}

    sens = by_text["0.125 1600dpi"]
    assert (sens["dpi"], sens["sensitivity"]) == (1600, 0.125)
    # Not when we fetched it: when the player last edited the command.
    assert sens["observed_at"] == "2026-08-07T08:58:41+00:00"
    assert sens["ref_url"] == "https://nightbot.tv/t/aplycs/commands"

    assert by_text["Viper V4 PRO"]["mouse_model"] == "Viper V4 PRO"
    assert by_text["MEIY PULSAR GLASSPAD"]["pad_brand"] == "MEIY"


def test_parse_is_rerunnable_without_duplicating(dsn, conn, store, player,
                                                 fake_nightbot, tmp_path):
    cli.cmd_fetch_nightbot(fetch_args(dsn, tmp_path))
    cli.cmd_parse_bot_commands(parse_args(dsn))
    cli.cmd_parse_bot_commands(parse_args(dsn))
    count = conn.execute(
        "SELECT count(*) AS n FROM settings_observations"
    ).fetchone()["n"]
    assert count == 3


def test_commands_for_an_unknown_channel_are_kept_but_not_attributed(
    dsn, conn, store, fake_nightbot, tmp_path
):
    """No player owns 'aplycs' here — the raw rows still land, so the parse
    can attribute them later once the roster resolves."""
    args = fetch_args(dsn, tmp_path)
    args.channels = "aplycs"
    cli.cmd_fetch_nightbot(args)
    cli.cmd_parse_bot_commands(parse_args(dsn))
    assert conn.execute("SELECT count(*) AS n FROM bot_commands").fetchone()["n"] == 3
    assert conn.execute(
        "SELECT count(*) AS n FROM settings_observations"
    ).fetchone()["n"] == 0
