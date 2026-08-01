"""Frame parser tests against real captured Twitch IRC lines."""

from mr_mouse_stats.twitch import frames


def real_lines(fixture_text):
    return [
        line
        for line in fixture_text("twitch_frames_real.txt").splitlines()
        if line.strip()
    ]


def test_real_nightbot_privmsg(fixture_text):
    line = frames.parse_line(real_lines(fixture_text)[0])
    msg = frames.chat_message(line)
    assert msg.channel == "caseoh_"
    assert msg.login == "nightbot"
    assert msg.display_name == "Nightbot"
    assert msg.user_id == "19264788"
    assert "bot-badge/1" in msg.badges
    assert "moderator/1" in msg.badges
    assert msg.msg_id == "2687407b-d39a-4e09-874b-6b304d47e777"
    assert msg.sent_ts_ms == 1785549695690
    assert msg.observed_at == "2026-08-01T02:01:35+00:00"
    assert msg.text.startswith("Subscribe to")


def test_real_plain_user_privmsg(fixture_text):
    msg = frames.chat_message(frames.parse_line(real_lines(fixture_text)[2]))
    assert msg.login == "nuken_leven"
    assert msg.badges == ()
    assert msg.text == "Aura to strong it's making me pass out"


def test_non_privmsg_lines_are_not_chat(fixture_text):
    for raw in real_lines(fixture_text)[3:]:  # ROOMSTATE, JOIN, 353, USERNOTICE
        line = frames.parse_line(raw)
        assert line is not None
        assert frames.chat_message(line) is None


def test_join_line_carries_channel_and_nick(fixture_text):
    line = frames.parse_line(real_lines(fixture_text)[4])
    assert line.command == "JOIN"
    assert line.params == ("#daesfps",)
    assert line.prefix_nick == "justinfan77712"


def test_real_escaped_tag_values(fixture_text):
    # USERNOTICE fixture has \s escapes in system-msg
    line = frames.parse_line(real_lines(fixture_text)[6])
    assert line.command == "USERNOTICE"
    assert line.tags["system-msg"].startswith("ZaneSwans subscribed at Tier 1.")


def test_tag_unescaping_rules():
    assert frames.unescape_tag_value(r"a\:b") == "a;b"
    assert frames.unescape_tag_value(r"a\sb") == "a b"
    assert frames.unescape_tag_value(r"a\\b") == "a\\b"
    assert frames.unescape_tag_value("a\\") == "a"  # lone trailing backslash
    assert frames.unescape_tag_value(r"\x") == "x"  # unknown escape drops backslash


def test_ping_line():
    line = frames.parse_line("PING :tmi.twitch.tv")
    assert line.command == "PING"
    assert line.params == ("tmi.twitch.tv",)
    assert line.prefix is None


def test_trailing_param_keeps_colons_and_spaces():
    line = frames.parse_line(
        ":a!a@a.tmi.twitch.tv PRIVMSG #chan :DPI: 800, sens: 6"
    )
    msg = frames.chat_message(line)
    assert msg.text == "DPI: 800, sens: 6"


def test_message_without_tags_has_no_timestamp():
    msg = frames.chat_message(
        frames.parse_line(":a!a@a.tmi.twitch.tv PRIVMSG #chan :hi")
    )
    assert msg.sent_ts_ms is None
    assert msg.observed_at is None


def test_blank_and_garbage_lines():
    assert frames.parse_line("") is None
    assert frames.parse_line("   ") is None
    assert frames.parse_line(":prefix.only") is None
