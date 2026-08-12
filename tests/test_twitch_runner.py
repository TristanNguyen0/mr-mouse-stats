"""Collection loop + parse pass, end to end against an in-memory DB.
No sockets, no network: the IRC client is replaced by a scripted fake."""

import threading

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


def stale_row(store):
    """The bot response's derived row, as an older parser would have left it.

    Stands in for the real regression: a parser that stopped a model name at
    the first digit run derived "Logitech G" from "logitech G502 X wireless",
    and incremental parsing never looked at that message again.
    """
    return store.conn.execute(
        "SELECT * FROM settings_observations WHERE raw_text LIKE '800 DPI%'"
    ).fetchone()


def test_reparse_rebuilds_rows_an_older_parser_got_wrong(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)
    assert cli.main(["parse-observations", "--db", dsn]) == 0
    capsys.readouterr()

    # Degrade the derived row the way a stale parser would have written it.
    store.conn.execute(
        "UPDATE settings_observations SET mouse_model = 'G', dpi = NULL "
        "WHERE raw_text LIKE '800 DPI%'"
    )
    store.commit()

    # A plain re-run cannot fix it: the message already has an observation.
    assert cli.main(["parse-observations", "--db", dsn]) == 0
    assert "0 observations parsed" in capsys.readouterr().out
    assert stale_row(store)["mouse_model"] == "G"

    assert cli.main(["parse-observations", "--db", dsn, "--reparse"]) == 0
    out = capsys.readouterr().out
    assert "2 derived rows replaced" in out
    assert "2 observations parsed" in out

    fixed = stale_row(store)
    assert fixed["mouse_model"] == "Starlight Pro"
    assert fixed["dpi"] == 800
    # Rebuilt, not appended alongside the stale row.
    count = store.conn.execute(
        "SELECT COUNT(*) c FROM settings_observations"
    ).fetchone()["c"]
    assert count == 2


def test_reparse_leaves_manual_observations_alone(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)
    message = store.conn.execute(
        "SELECT * FROM twitch_messages WHERE kind = 'bot_response'"
    ).fetchone()
    player_id = store.player_ids_by_twitch_channel()["shpeediry"]
    store.add_settings_observation(
        player_id, message["observed_at"], "manual",
        channel=message["channel"], raw_text=message["text"],
        dpi=400, source_message_id=message["id"],
    )
    store.commit()

    assert cli.main(["parse-observations", "--db", dsn, "--reparse"]) == 0
    capsys.readouterr()

    rows = store.conn.execute(
        "SELECT * FROM settings_observations ORDER BY id"
    ).fetchall()
    # The hand-recorded row survives, and the message it covers was not
    # parsed over — only the broadcaster's separate response was derived.
    assert [r["source"] for r in rows] == ["manual", "twitch_chat"]
    assert rows[0]["dpi"] == 400
    assert rows[1]["dpi"] == 1600


def test_reparse_dry_run_deletes_nothing(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)
    assert cli.main(["parse-observations", "--db", dsn]) == 0
    capsys.readouterr()
    before = [dict(r) for r in store.conn.execute(
        "SELECT * FROM settings_observations ORDER BY id"
    ).fetchall()]

    assert cli.main(["parse-observations", "--db", dsn, "--reparse", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 derived rows replaced" in out  # what it would replace
    assert "dry run, nothing written" in out

    after = [dict(r) for r in store.conn.execute(
        "SELECT * FROM settings_observations ORDER BY id"
    ).fetchall()]
    assert after == before


def test_parse_observations_dry_run(store, dsn, capsys):
    collect(FakeClient(SCRIPT), store)
    assert cli.main(["parse-observations", "--db", dsn, "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    conn = db.connect(dsn)
    count = conn.execute("SELECT COUNT(*) c FROM settings_observations").fetchone()["c"]
    assert count == 0
    conn.close()


def test_service_deriver_turns_captures_into_observations(store, dsn, monkeypatch):
    """The gap that let production collect for days without deriving anything:
    the hosted task ran the collector and the scrape, and nothing else."""
    from mr_mouse_stats.service import Deriver

    monkeypatch.setenv("MR_MOUSE_STATS_DB", dsn)
    collect(FakeClient(SCRIPT), store)

    counts = Deriver(threading.Event()).run_once()

    assert counts["parsed"] == 2
    rows = store.conn.execute(
        "SELECT * FROM settings_observations ORDER BY id"
    ).fetchall()
    assert [r["source"] for r in rows] == ["twitch_chat", "twitch_chat"]

    # Idempotent: the timer fires every few minutes over the same history.
    assert Deriver(threading.Event()).run_once()["parsed"] == 0


def clock_at(*values):
    """Scripted clock that holds its final value instead of running out."""
    remaining = iter(values)
    current = [0.0]

    def tick():
        try:
            current[0] = next(remaining)
        except StopIteration:
            pass
        return current[0]

    return tick


class RecordingClient(FakeClient):
    """FakeClient that also tracks joined channels, like the real client."""

    def __init__(self, messages, channels):
        super().__init__(messages)
        self.channels = list(channels)
        self.joined = []

    def join(self, channels):
        added = [c for c in channels if c not in self.channels]
        if not added:  # mirrors ReadOnlyIrcClient.join's early return
            return []
        self.channels.extend(added)
        self.joined.append(added)
        return added


def test_collect_joins_handles_added_after_startup(store):
    """An admin handle fix is picked up without restarting the collector."""
    client = RecordingClient([None] * 4, channels=["shpeediry"])
    # a handle corrected in the dashboard while the collector is running
    pid = store.upsert_player_stub("Trqstme", "resolved", "t0")
    store.record_social_account(pid, "twitch", "trqstmemr", None, "t0", source="manual")
    store.commit()

    # clock crosses CHANNEL_REFRESH_INTERVAL on the second tick
    collect(client, store, clock=clock_at(0.0, 400.0))

    assert client.joined == [["trqstmemr"]]
    assert "trqstmemr" in client.channels


def test_collect_does_not_rejoin_known_channels(store):
    client = RecordingClient([None] * 4, channels=["shpeediry"])
    collect(client, store, clock=clock_at(0.0, 400.0))
    assert client.joined == []  # shpeediry already joined; nothing new


def test_collect_survives_a_failing_channel_refresh(store):
    class Broken:
        def player_ids_by_twitch_channel(self):
            raise RuntimeError("database went away")

        def __getattr__(self, name):
            return getattr(store, name)

    client = RecordingClient([None] * 4, channels=["shpeediry"])
    # must not propagate: losing the refresh is survivable, losing the loop is not
    collect(client, Broken(), clock=clock_at(0.0, 400.0))
    assert client.joined == []


def test_dry_run_does_not_refresh_channels():
    client = RecordingClient([None] * 4, channels=["shpeediry"])
    collect(client, store=None, clock=clock_at(0.0, 400.0))
    assert client.joined == []
