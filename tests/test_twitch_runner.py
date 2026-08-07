"""Collection loop + parse pass, end to end against an in-memory DB.
No sockets, no network: the IRC client is replaced by a scripted fake."""

import pytest

from mr_mouse_stats import cli, db
from mr_mouse_stats.twitch.frames import ChatMessage
from mr_mouse_stats.twitch.runner import collect

BASE_TS = 1_785_000_000_000


def msg(channel="shpeediry", login="viewer1", text="hi", ts=0, msg_id=None, badges=()):
    return ChatMessage(
        channel=channel, login=login, text=text, msg_id=msg_id,
        sent_ts_ms=BASE_TS + ts, badges=tuple(badges),
    )


class FakeClient:
    """Yields a fixed script, then ends (real client never ends)."""

    def __init__(self, messages):
        self.messages = messages
        self.unconfirmed_joins = set()

    def run(self):
        yield from self.messages


SCRIPT = [
    msg(text="hello there", ts=0),                                    # chatter
    msg(text="!dpi", ts=1_000, msg_id="t-1"),                          # trigger
    None,                                                              # heartbeat
    msg(login="nightbot", text="800 DPI, 6 sens, Finalmouse Starlight Pro",
        ts=3_000, msg_id="r-1", badges=("bot-badge/1", "moderator/1")),
    msg(login="shpeediry", text="actually on 1600 dpi now", ts=5_000, msg_id="r-2"),
    msg(login="viewer2", text="ty", ts=6_000),                         # in window, ignored
    msg(login="nightbot", text="unrelated later msg", ts=60_000),      # window expired
]


@pytest.fixture
def store(conn):
    store = db.Store(conn)
    pid = store.upsert_player_stub("Shpeediry", "resolved", "t0")
    store.record_social_account(pid, "twitch", "Shpeediry", None, "t0")
    store.commit()
    return store


def test_collect_persists_triggers_and_responses(store):
    stats = collect(FakeClient(SCRIPT), store)
    assert stats["trigger"] == 1
    assert stats["bot_response"] == 1
    assert stats["broadcaster_response"] == 1
    assert stats["messages_seen"] == 6  # heartbeat not counted

    rows = store.conn.execute(
        "SELECT * FROM twitch_messages ORDER BY id"
    ).fetchall()
    assert [r["kind"] for r in rows] == [
        "trigger", "bot_response", "broadcaster_response",
    ]
    trigger, bot, broadcaster = rows
    assert bot["trigger_id"] == trigger["id"]
    assert broadcaster["trigger_id"] == trigger["id"]
    assert bot["badges"] == "bot-badge/1,moderator/1"
    assert trigger["observed_at"].startswith("2026-")


def test_collect_dry_run_writes_nothing(store):
    stats = collect(FakeClient(SCRIPT), store=None)
    assert stats["trigger"] == 1  # events still observed and counted


def test_collect_duration_cutoff(store):
    clock_values = iter([0.0, 100.0, 100.0, 100.0, 100.0])
    stats = collect(
        FakeClient(SCRIPT), store, duration=50.0, clock=lambda: next(clock_values)
    )
    assert stats["messages_seen"] == 0  # cut off before first message processed


def test_parse_observations_end_to_end(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)  # commits per message
    assert cli.main(["parse-observations", "--db", dsn]) == 0
    out = capsys.readouterr().out
    assert "2 observations parsed" in out  # bot + broadcaster responses

    conn = db.connect(dsn)
    rows = conn.execute(
        "SELECT * FROM settings_observations ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["source"] == "twitch_chat"
    assert rows[0]["dpi"] == 800
    assert rows[0]["sensitivity"] == 6.0
    assert rows[0]["mouse_brand"] == "Finalmouse"
    assert rows[0]["raw_text"].startswith("800 DPI")
    assert rows[0]["source_message_id"] is not None
    assert rows[1]["dpi"] == 1600  # broadcaster's own answer

    conn.close()

    # re-run: nothing new (raw messages already derived)
    assert cli.main(["parse-observations", "--db", dsn]) == 0
    assert "0 observations parsed" in capsys.readouterr().out
    conn2 = db.connect(dsn)
    count = conn2.execute("SELECT COUNT(*) c FROM settings_observations").fetchone()["c"]
    assert count == 2
    conn2.close()


def test_parse_observations_dry_run(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)
    assert cli.main(["parse-observations", "--db", dsn, "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    conn = db.connect(dsn)
    count = conn.execute("SELECT COUNT(*) c FROM settings_observations").fetchone()["c"]
    assert count == 0
    conn.close()
