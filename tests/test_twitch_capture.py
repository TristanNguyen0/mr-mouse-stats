from mr_mouse_stats.twitch import detect
from mr_mouse_stats.twitch.capture import (
    KIND_BOT,
    KIND_BROADCASTER,
    KIND_TRIGGER,
    Correlator,
)
from mr_mouse_stats.twitch.frames import ChatMessage


def msg(channel="shpeediry", login="viewer1", text="hi", ts=0, badges=()):
    return ChatMessage(
        channel=channel, login=login, text=text,
        sent_ts_ms=1_785_000_000_000 + ts, badges=tuple(badges),
    )


class TestDetect:
    def test_trigger_variants(self):
        assert detect.trigger_command("!dpi") == "dpi"
        assert detect.trigger_command("!Sens") == "sens"
        assert detect.trigger_command("!SENSITIVITY") == "sensitivity"
        assert detect.trigger_command("  !mouse  ") == "mouse"
        assert detect.trigger_command("!settings please") == "settings"
        assert detect.trigger_command("!gear?") == "gear"

    def test_non_triggers(self):
        assert detect.trigger_command("what dpi do you use") is None
        assert detect.trigger_command("!dpix") is None  # word boundary
        assert detect.trigger_command("!sensei") is None
        assert detect.trigger_command("dpi 800") is None
        assert detect.trigger_command("") is None

    def test_bot_by_login_and_badge(self):
        assert detect.is_bot("nightbot")
        assert detect.is_bot("StreamElements")
        assert detect.is_bot("randomuser", ("bot-badge/1",))
        assert not detect.is_bot("randomuser", ("moderator/1",))


class TestCorrelator:
    def test_trigger_then_bot_response(self):
        c = Correlator()
        [trigger_event] = c.feed(msg(text="!dpi", ts=0))
        assert trigger_event.kind == KIND_TRIGGER
        assert trigger_event.command == "dpi"
        [response] = c.feed(
            msg(login="nightbot", text="800 DPI, 6 sens", ts=3_000)
        )
        assert response.kind == KIND_BOT
        assert response.trigger is trigger_event.message

    def test_response_after_window_ignored(self):
        c = Correlator(window_ms=20_000)
        c.feed(msg(text="!sens", ts=0))
        assert c.feed(msg(login="nightbot", text="6 sens", ts=25_000)) == []
        # window is also cleaned up: a later bot msg doesn't match either
        assert c.feed(msg(login="nightbot", text="6 sens", ts=26_000)) == []

    def test_bot_message_without_trigger_ignored(self):
        c = Correlator()
        assert c.feed(msg(login="nightbot", text="follow us on twitter!")) == []

    def test_broadcaster_reply_in_window(self):
        c = Correlator()
        c.feed(msg(channel="shpeediry", text="!sens", ts=0))
        [event] = c.feed(
            msg(channel="shpeediry", login="shpeediry", text="1600 dpi 3 sens", ts=5_000)
        )
        assert event.kind == KIND_BROADCASTER

    def test_plain_viewer_chatter_in_window_ignored(self):
        c = Correlator()
        c.feed(msg(text="!dpi", ts=0))
        assert c.feed(msg(login="viewer2", text="nice aim", ts=1_000)) == []

    def test_windows_are_per_channel(self):
        c = Correlator()
        c.feed(msg(channel="alpha", text="!dpi", ts=0))
        assert c.feed(msg(channel="beta", login="nightbot", text="800 dpi", ts=1_000)) == []

    def test_new_trigger_refreshes_window(self):
        c = Correlator(window_ms=20_000)
        c.feed(msg(text="!dpi", ts=0))
        [t2] = c.feed(msg(login="viewer2", text="!mouse", ts=15_000))
        [response] = c.feed(msg(login="nightbot", text="Lamzu Maya", ts=30_000))
        assert response.trigger is t2.message  # tied to the latest trigger

    def test_message_without_timestamp_ignored(self):
        c = Correlator()
        no_ts = ChatMessage(channel="x", login="v", text="!dpi")
        assert c.feed(no_ts) == []
