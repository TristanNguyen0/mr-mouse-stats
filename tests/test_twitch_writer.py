"""Write-path resilience.

Twitch captures cannot be re-fetched, so the contract these tests pin down
is: a database failure delays writes, it never loses them, and it never
propagates into the collection loop.
"""

import pytest

from mr_mouse_stats import db
from mr_mouse_stats.twitch.capture import CaptureEvent
from mr_mouse_stats.twitch.frames import ChatMessage
from mr_mouse_stats.twitch.runner import collect
from mr_mouse_stats.twitch.writer import CaptureWriter

BASE_TS = 1_785_000_000_000


def msg(text="hi", login="viewer1", ts=0, msg_id=None, badges=()):
    return ChatMessage(
        channel="shpeediry", login=login, text=text, msg_id=msg_id,
        sent_ts_ms=BASE_TS + ts, badges=tuple(badges),
    )


def trigger_event(msg_id="t-1"):
    return CaptureEvent("trigger", msg(text="!dpi", msg_id=msg_id), command="!dpi")


def response_event(trigger, msg_id="r-1"):
    return CaptureEvent(
        "bot_response",
        msg(text="800 dpi 6 sens", login="nightbot", ts=2_000, msg_id=msg_id),
        trigger=trigger.message,
    )


class FlakyStore:
    """Wraps a real Store, failing the first `fail_times` write attempts."""

    def __init__(self, store, fail_times):
        self._store = store
        self.remaining = fail_times
        self.rollbacks = 0

    def record_twitch_message(self, **kwargs):
        if self.remaining:
            self.remaining -= 1
            raise RuntimeError("connection to Neon lost")
        return self._store.record_twitch_message(**kwargs)

    def commit(self):
        self._store.commit()

    def rollback(self):
        self.rollbacks += 1
        self._store.rollback()

    def __getattr__(self, name):
        return getattr(self._store, name)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_successful_write_clears_the_spool(store):
    writer = CaptureWriter(store)
    writer.submit(trigger_event())
    assert writer.pending == 0
    assert writer.written == 1
    assert store.conn.execute(
        "SELECT COUNT(*) c FROM twitch_messages"
    ).fetchone()["c"] == 1


def test_failed_write_keeps_the_capture_spooled(store):
    flaky = FlakyStore(store, fail_times=1)
    writer = CaptureWriter(flaky)
    writer.submit(trigger_event())
    assert writer.pending == 1  # held, not lost
    assert writer.written == 0
    assert flaky.rollbacks == 1
    assert store.conn.execute(
        "SELECT COUNT(*) c FROM twitch_messages"
    ).fetchone()["c"] == 0


def test_spool_drains_when_the_database_recovers(store):
    clock = Clock()
    flaky = FlakyStore(store, fail_times=1)
    writer = CaptureWriter(flaky, clock=clock)
    writer.submit(trigger_event("t-1"))
    writer.submit(trigger_event("t-2"))
    assert writer.pending == 2

    clock.now = 100.0  # past the backoff
    assert writer.flush() == 2
    assert writer.pending == 0
    rows = store.conn.execute(
        "SELECT msg_id FROM twitch_messages ORDER BY id"
    ).fetchall()
    assert [r["msg_id"] for r in rows] == ["t-1", "t-2"]


def test_backoff_prevents_hammering_a_down_database(store):
    clock = Clock()
    flaky = FlakyStore(store, fail_times=99)
    writer = CaptureWriter(flaky, clock=clock)
    writer.submit(trigger_event())
    attempts_after_first = flaky.rollbacks

    writer.flush()  # too soon: should not even try
    assert flaky.rollbacks == attempts_after_first

    clock.now = 100.0
    writer.flush()
    assert flaky.rollbacks == attempts_after_first + 1


def test_trigger_links_survive_a_deferred_flush(store):
    """A response spooled alongside its trigger still resolves trigger_id."""
    clock = Clock()
    flaky = FlakyStore(store, fail_times=1)
    writer = CaptureWriter(flaky, clock=clock)
    trigger = trigger_event()
    writer.submit(trigger)
    writer.submit(response_event(trigger))
    assert writer.pending == 2

    clock.now = 100.0
    writer.flush()
    rows = store.conn.execute(
        "SELECT id, kind, trigger_id FROM twitch_messages ORDER BY id"
    ).fetchall()
    assert rows[1]["trigger_id"] == rows[0]["id"]


def test_spool_is_bounded_and_drops_oldest(store):
    flaky = FlakyStore(store, fail_times=99)
    writer = CaptureWriter(flaky, max_spool=3, clock=Clock())
    for i in range(5):
        writer.submit(trigger_event(f"t-{i}"))
    assert writer.pending == 3
    assert writer.dropped == 2  # loudly logged; memory beats total loss


class FailingClient:
    """Yields captures then stops, like the scripted fakes elsewhere."""

    def __init__(self, messages):
        self.messages = messages
        self.channels = ["shpeediry"]
        self.unconfirmed_joins = set()

    def run(self):
        yield from self.messages

    def join(self, channels):
        return []


def test_collection_loop_survives_a_dead_database(store):
    """The acceptance test for Phase 5: the loop must not die."""
    flaky = FlakyStore(store, fail_times=99)
    script = [
        msg(text="!dpi", ts=1_000, msg_id="t-1"),
        msg(login="nightbot", text="800 DPI, 6 sens", ts=3_000, msg_id="r-1",
            badges=("bot-badge/1",)),
    ]
    stats = collect(FailingClient(script), flaky)  # must not raise
    assert stats["messages_seen"] == 2
    assert stats["trigger"] == 1
    assert stats["unwritten"] == 2  # held for a retry that never came
    assert stats["written"] == 0


def test_stop_signal_drains_and_exits(store):
    stopped = {"value": False}
    script = [msg(text="!dpi", ts=1_000, msg_id="t-1")] + [None] * 5

    def should_stop():
        # stop once the first message has been processed
        return stopped["value"]

    class Client(FailingClient):
        def run(self):
            for item in self.messages:
                yield item
                stopped["value"] = True

    stats = collect(Client(script), store, should_stop=should_stop)
    assert stats["written"] == 1
    assert stats["unwritten"] == 0  # final drain flushed before exit
